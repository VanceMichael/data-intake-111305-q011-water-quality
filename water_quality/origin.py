import copy
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def load_scenario(path=None):
    if path is not None:
        scenario = Path(path)
    else:
        scenario = Path(__file__).parent / "fixtures" / "origin.json"
        if not scenario.is_file():
            scenario = Path(__file__).parents[1] / "fixtures" / "origin.json"
    return json.loads(scenario.read_text(encoding="utf-8"))


class Origin(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, scenario):
        self.scenario = copy.deepcopy(scenario)
        self.offsets = {}
        self.requests = []
        self.receipts = {}
        self.state_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), Handler)

    @property
    def base_url(self):
        return "http://127.0.0.1:" + str(self.server_port)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format_string, *args):
        return None

    def send_payload(self, status, headers, body):
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, str(value))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlsplit(self.path).path
        with self.server.state_lock:
            self.server.requests.append({"method": "GET", "path": self.path, "headers": dict(self.headers)})
            if path == "/portal/status":
                identity = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
                payload = {"id": identity, "receipt": self.server.receipts.get(identity)}
                self.send_payload(200, {"Content-Type": "application/json"}, json.dumps(payload).encode())
                return
            choices = self.server.scenario["routes"].get(path, {"status": 404, "body": "unknown route"})
            if isinstance(choices, list):
                offset = self.server.offsets.get(path, 0)
                self.server.offsets[path] = offset + 1
                selected = choices[min(offset, len(choices) - 1)]
            else:
                selected = choices
            selected = copy.deepcopy(selected)
        if "json" in selected:
            body = json.dumps(selected["json"], ensure_ascii=False).encode()
            content_type = "application/json"
        else:
            body = selected.get("body", "").encode("utf-8")
            content_type = selected.get("content_type", "text/plain; charset=utf-8")
        headers = {"Content-Type": content_type, **selected.get("headers", {})}
        status = selected.get("status", 200)
        if self.headers.get("If-None-Match") and self.headers["If-None-Match"] == headers.get("ETag"):
            self.send_payload(304, headers, b"")
            return
        byte_range = self.headers.get("Range", "")
        if byte_range and selected.get("range", False):
            try:
                start_text, end_text = byte_range.removeprefix("bytes=").split("-", 1)
                if not byte_range.startswith("bytes="):
                    raise ValueError("invalid range unit")
                start = int(start_text)
                end = int(end_text) if end_text else len(body) - 1
                if not 0 <= start <= end < len(body):
                    raise ValueError("invalid range bounds")
            except ValueError:
                self.send_payload(416, {"Content-Range": "bytes */" + str(len(body))}, b"")
                return
            headers["Content-Range"] = f"bytes {start}-{end}/{len(body)}"
            body, status = body[start:end + 1], 206
        if selected.get("truncate"):
            self.send_response(status)
            self.send_header("Content-Length", str(len(body) + 5))
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
            return
        self.send_payload(status, headers, body)

    def do_POST(self):
        path = urlsplit(self.path).path
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        fields = {name: values[0] for name, values in parse_qs(raw.decode()).items()}
        with self.server.state_lock:
            self.server.requests.append({"method": "POST", "path": path, "fields": fields})
            if path != "/portal/submit":
                status, payload = 404, {"error": "unknown_form"}
            elif fields.get("csrf") != self.server.scenario["portal"]["csrf"]:
                status, payload = 409, {"error": "stale_form"}
            elif not fields.get("id"):
                status, payload = 422, {"error": "identity_required"}
            else:
                receipt = "ACK-" + str(len(self.server.receipts) + 1)
                self.server.receipts[fields["id"]] = receipt
                if fields.get("lose_response") == "yes":
                    self.close_connection = True
                    return
                status, payload = 200, {"id": fields["id"], "receipt": receipt}
        self.send_payload(status, {"Content-Type": "application/json"}, json.dumps(payload).encode())


@contextmanager
def serve(scenario=None):
    server = Origin(scenario or load_scenario())
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
