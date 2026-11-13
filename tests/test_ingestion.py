import http.client
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from water_quality.archive import Archive
from water_quality.client import Client, collect
from water_quality.decoding import parse_page, records
from water_quality.origin import load_scenario, serve


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "journal.sqlite3"
        self.archive = Archive(self.path)

    def tearDown(self):
        self.archive.close()
        self.temporary.cleanup()

    def test_repeated_content_keeps_separate_observations(self):
        first = self.archive.append("a", "/one", 200, {"etag": "old"}, b"same")
        second = self.archive.append("a", "/two", 200, {"etag": "new"}, b"same")
        self.assertNotEqual(first, second)
        self.assertEqual(self.archive.connection.execute("SELECT count(*) FROM bodies").fetchone()[0], 1)
        self.assertEqual(len(self.archive.captures()), 2)
        self.assertEqual(self.archive.captures()[1]["headers"]["etag"], "new")

    def test_persists_binary_body_across_reopen(self):
        capture_id = self.archive.append("a", "/data", 200, {}, bytes([0, 255, 17]))
        self.archive.close()
        self.archive = Archive(self.path)
        self.assertEqual(self.archive.body(capture_id), bytes([0, 255, 17]))

    def test_missing_capture_is_not_empty_content(self):
        with self.assertRaises(KeyError):
            self.archive.body(999)

    def test_invalid_metadata_does_not_write(self):
        for source, status in [("", 200), ("a", True), ("a", 999)]:
            with self.subTest(source=source, status=status), self.assertRaises(ValueError):
                self.archive.append(source, "/", status, {}, b"body")
        self.assertEqual(self.archive.captures(), [])

    def test_transaction_rolls_back_body_on_capture_failure(self):
        self.archive.connection.execute("CREATE TRIGGER reject_capture BEFORE INSERT ON captures BEGIN SELECT RAISE(ABORT,'blocked'); END")
        with self.assertRaisesRegex(Exception, "blocked"):
            self.archive.append("a", "/", 200, {}, b"body")
        self.assertEqual(self.archive.connection.execute("SELECT count(*) FROM bodies").fetchone()[0], 0)

    def test_integrity_mismatch_is_detected(self):
        capture_id = self.archive.append("a", "/", 200, {}, b"before")
        with self.archive.connection:
            self.archive.connection.execute("UPDATE bodies SET content=?", (b"after",))
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            self.archive.body(capture_id)

    def test_sources_remain_separate(self):
        self.archive.append("alpha", "/", 200, {}, b"a")
        self.archive.append("beta", "/", 200, {}, b"b")
        self.assertEqual(len(self.archive.captures("alpha")), 1)


class ProtocolTests(unittest.TestCase):
    def test_feed_roundtrip_retains_source_identity(self):
        scenario = load_scenario()
        with serve(scenario) as origin, tempfile.TemporaryDirectory() as temporary:
            archive = Archive(Path(temporary) / "raw.sqlite3")
            try:
                capture_id, response = collect(Client(origin.base_url), archive, "fixture")
                observed = records(response.body, response.headers["content-type"])
                expected = scenario["source_records"]
                self.assertEqual(len(observed), len(expected))
                for item, original in zip(observed, expected):
                    for name in scenario["identity_fields"]:
                        self.assertEqual(str(item[name]), str(original[name]))
                self.assertEqual(archive.body(capture_id), response.body)
            finally:
                archive.close()

    def test_non_success_keeps_retry_metadata(self):
        with serve() as origin:
            response = Client(origin.base_url).fetch("/rate-limited")
            self.assertEqual(response.status, 429)
            self.assertEqual(response.headers["retry-after"], "2")

    def test_server_script_changes_only_when_called(self):
        with serve() as origin:
            client = Client(origin.base_url)
            self.assertEqual(client.fetch("/unstable").status, 503)
            self.assertEqual(client.fetch("/unstable").status, 200)
            self.assertEqual(client.fetch("/unstable").status, 200)
            self.assertEqual(len(origin.requests), 3)

    def test_conditional_get_and_unknown_route(self):
        with serve() as origin:
            client = Client(origin.base_url)
            self.assertEqual(client.fetch("/feed", {"If-None-Match": "feed-1"}).status, 304)
            self.assertEqual(client.fetch("/not-found").status, 404)

    def test_range_and_ignored_range_are_distinguishable(self):
        with serve() as origin:
            client = Client(origin.base_url)
            partial = client.fetch("/range", {"Range": "bytes=2-4"})
            self.assertEqual((partial.status, partial.body), (206, b"234"))
            self.assertEqual(partial.headers["content-range"], "bytes 2-4/10")
            self.assertEqual(client.fetch("/range", {"Range": "bytes=30-"}).status, 416)
            self.assertEqual(client.fetch("/range-ignored", {"Range": "bytes=2-4"}).body, b"0123456789")

    def test_truncated_response_is_not_archived_as_complete(self):
        with serve() as origin, tempfile.TemporaryDirectory() as temporary:
            archive = Archive(Path(temporary) / "raw.sqlite3")
            try:
                with self.assertRaises(http.client.IncompleteRead):
                    collect(Client(origin.base_url), archive, "fixture", "/truncated")
                self.assertEqual(archive.captures(), [])
            finally:
                archive.close()

    def test_origin_relative_paths_only(self):
        client = Client("http://127.0.0.1:1")
        for path in ("https://example.invalid/x", "//example.invalid/x", "relative"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                client.fetch(path)

    def test_fixture_servers_have_independent_state(self):
        with serve() as first, serve() as second:
            self.assertEqual(Client(first.base_url).fetch("/unstable").status, 503)
            self.assertEqual(Client(first.base_url).fetch("/unstable").status, 200)
            self.assertEqual(Client(second.base_url).fetch("/unstable").status, 503)

    def test_portal_rejects_stale_tokens_without_receipt(self):
        with serve() as origin:
            request = Request(origin.base_url + "/portal/submit", data=urlencode({"id": "A1", "csrf": "stale"}).encode())
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=2)
            self.assertEqual(caught.exception.code, 409)
            self.assertIsNone(json.loads(Client(origin.base_url).fetch("/portal/status?id=A1").body)["receipt"])

    def test_portal_can_accept_before_losing_response(self):
        scenario = load_scenario()
        with serve(scenario) as origin:
            fields = {"id": "A1", "csrf": scenario["portal"]["csrf"], "lose_response": "yes"}
            request = Request(origin.base_url + "/portal/submit", data=urlencode(fields).encode())
            with self.assertRaises(http.client.RemoteDisconnected):
                urlopen(request, timeout=2)
            response = Client(origin.base_url).fetch("/portal/status?id=A1")
            self.assertEqual(json.loads(response.body)["receipt"], "ACK-1")


class DecoderTests(unittest.TestCase):
    def test_json_requires_record_objects(self):
        for body in (b"{}", b"[1]", b"null"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                records(body, "application/json")

    def test_csv_reordered_columns_preserves_names(self):
        parsed = records("name,key\n中文,A1\n".encode(), "text/csv; charset=utf-8")
        self.assertEqual(parsed, [{"name": "中文", "key": "A1"}])

    def test_page_reads_hidden_controls_and_embedded_records(self):
        body = b'<form action="/submit"><input name="csrf" value="a&amp;b"></form><script id="source-records" type="application/json">[{"id":"A1"}]</script>'
        self.assertEqual(parse_page(body).forms[0]["fields"]["csrf"], "a&b")
        self.assertEqual(records(body, "text/html"), [{"id": "A1"}])

    def test_unsupported_media_does_not_become_empty_feed(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            records(b"arbitrary", "application/octet-stream")


if __name__ == "__main__":
    unittest.main()
