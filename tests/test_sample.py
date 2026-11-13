import unittest
from water_quality.sample import inspect_record

class RecordTests(unittest.TestCase):
    def test_normalizes_identity_and_preserves_payload(self):
        result = inspect_record({"source": " feed ", "key": " item-1 ", "revision": 2, "value": {"name": "sample"}})
        self.assertEqual(result["source"], "feed")
        self.assertEqual(result["key"], "item-1")
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["value"], {"name": "sample"})
        self.assertEqual(result["event"], "measurement_semantics")

    def test_rejects_missing_identity(self):
        with self.assertRaisesRegex(ValueError, "missing fields"):
            inspect_record({"source": "feed"})

    def test_rejects_invalid_revision(self):
        for revision in (-1, True, "2"):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                inspect_record({"source": "feed", "key": "k", "revision": revision, "value": None})

if __name__ == "__main__":
    unittest.main()
