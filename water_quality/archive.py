import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Archive:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS bodies (
                digest TEXT PRIMARY KEY,
                content BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS captures (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                url TEXT NOT NULL,
                status INTEGER NOT NULL,
                headers TEXT NOT NULL,
                digest TEXT NOT NULL REFERENCES bodies(digest),
                received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
        """)

    def close(self):
        self.connection.close()

    @contextmanager
    def transaction(self):
        with self.connection:
            yield self.connection

    def append(self, source, url, status, headers, body):
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source is required")
        if not isinstance(body, bytes):
            raise TypeError("body must be bytes")
        if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
            raise ValueError("invalid HTTP status")
        digest = hashlib.sha256(body).hexdigest()
        encoded_headers = json.dumps(dict(headers), ensure_ascii=False, sort_keys=True)
        with self.transaction() as connection:
            connection.execute("INSERT OR IGNORE INTO bodies VALUES (?, ?)", (digest, body))
            cursor = connection.execute(
                "INSERT INTO captures(source,url,status,headers,digest) VALUES (?,?,?,?,?)",
                (source, url, status, encoded_headers, digest),
            )
        return cursor.lastrowid

    def captures(self, source=None):
        query = "SELECT * FROM captures"
        params = ()
        if source is not None:
            query += " WHERE source=?"
            params = (source,)
        rows = self.connection.execute(query + " ORDER BY id", params).fetchall()
        return [{**dict(row), "headers": json.loads(row["headers"])} for row in rows]

    def body(self, capture_id):
        row = self.connection.execute(
            "SELECT b.digest,b.content FROM bodies b JOIN captures c ON c.digest=b.digest WHERE c.id=?",
            (capture_id,),
        ).fetchone()
        if row is None:
            raise KeyError(capture_id)
        content = bytes(row["content"])
        if hashlib.sha256(content).hexdigest() != row["digest"]:
            raise ValueError("archived content digest mismatch")
        return content
