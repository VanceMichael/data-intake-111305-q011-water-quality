from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    headers: dict
    body: bytes


class Client:
    def __init__(self, base_url, timeout=3):
        parsed = urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("HTTP base URL required")
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout

    def fetch(self, path, headers=None):
        if not path.startswith("/") or path.startswith("//") or urlsplit(path).netloc:
            raise ValueError("source path must be origin-relative")
        request = Request(urljoin(self.base_url, path), headers=dict(headers or {}))
        try:
            response = urlopen(request, timeout=self.timeout)
        except HTTPError as error:
            response = error
        with response:
            return Response(
                response.geturl(), response.status,
                {name.lower(): value for name, value in response.headers.items()},
                response.read(),
            )


def collect(client, archive, source, path="/feed"):
    response = client.fetch(path)
    capture_id = archive.append(source, response.url, response.status, response.headers, response.body)
    return capture_id, response
