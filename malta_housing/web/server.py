"""Local HTTP server for browsing the SQLite listings database."""

from __future__ import annotations

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from malta_housing.common import configure_stdio
from malta_housing.db import queries
from malta_housing.db.store import init_db, set_listing_hidden
from malta_housing.paths import DB_PATH, PACKAGE_ROOT

STATIC_DIR = PACKAGE_ROOT / "web" / "static"


def _json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


def _parse_int(values: list[str] | None) -> int | None:
    if not values:
        return None
    try:
        return int(values[0])
    except ValueError:
        return None


def _parse_bool(values: list[str] | None) -> bool | None:
    if not values:
        return None
    return values[0].lower() in {"1", "true", "yes", "on"}


class BrowseHandler(BaseHTTPRequestHandler):
    server_version = "MaltaHousingBrowse/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)

        if path in {"/", "/index.html"}:
            self._serve_file(STATIC_DIR / "index.html")
            return
        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            # Prevent path traversal
            target = (STATIC_DIR / rel).resolve()
            if not str(target).startswith(str(STATIC_DIR.resolve())):
                self._send(*_json_bytes({"error": "Not found"}, 404))
                return
            self._serve_file(target)
            return

        if path == "/api/stats":
            self._send(*_json_bytes(queries.get_stats()))
            return

        if path == "/api/listings":
            payload = queries.list_listings(
                q=(qs.get("q") or [None])[0] or None,
                locality=(qs.get("locality") or [None])[0] or None,
                source=(qs.get("source") or [None])[0] or None,
                seller_type=(qs.get("seller_type") or [None])[0] or None,
                property_type=(qs.get("property_type") or [None])[0] or None,
                min_price=_parse_int(qs.get("min_price")),
                max_price=_parse_int(qs.get("max_price")),
                freehold=_parse_bool(qs.get("freehold")),
                airspace=_parse_bool(qs.get("airspace")),
                show_hidden=_parse_bool(qs.get("show_hidden")) is True,
                sort=(qs.get("sort") or ["updated_desc"])[0],
                limit=_parse_int(qs.get("limit")) or 100,
                offset=_parse_int(qs.get("offset")) or 0,
            )
            self._send(*_json_bytes(payload))
            return

        match = re.fullmatch(r"/api/listings/(\d+)", path)
        if match:
            listing = queries.get_listing(int(match.group(1)))
            if listing is None:
                self._send(*_json_bytes({"error": "Listing not found"}, 404))
                return
            self._send(*_json_bytes(listing))
            return

        match_hist = re.fullmatch(r"/api/listings/(\d+)/history", path)
        if match_hist:
            history = queries.get_price_history(int(match_hist.group(1)))
            self._send(*_json_bytes({"items": history}))
            return

        self._send(*_json_bytes({"error": "Not found"}, 404))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        match = re.fullmatch(r"/api/listings/(\d+)/hidden", path)
        if match:
            listing_id = int(match.group(1))
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send(*_json_bytes({"error": "Invalid JSON"}, 400))
                return
            if "hidden" not in body:
                self._send(*_json_bytes({"error": "Missing 'hidden' boolean"}, 400))
                return
            hidden = bool(body["hidden"])
            if not set_listing_hidden(listing_id, hidden):
                self._send(*_json_bytes({"error": "Listing not found"}, 404))
                return
            listing = queries.get_listing(listing_id)
            self._send(*_json_bytes(listing or {"id": listing_id, "is_hidden": hidden}))
            return

        self._send(*_json_bytes({"error": "Not found"}, 404))

    def _serve_file(self, path: Path) -> None:
        if not path.is_file():
            self._send(*_json_bytes({"error": "Not found"}, 404))
            return
        content_type, _ = mimetypes.guess_type(str(path))
        if content_type is None:
            content_type = "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type = f"{content_type}; charset=utf-8"
        data = path.read_bytes()
        self._send(200, data, content_type)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    configure_stdio()
    init_db()
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), BrowseHandler)
    print(f"🌐 Malta Housing browser: http://{host}:{port}", flush=True)
    print(f"   Database: {DB_PATH}", flush=True)
    print("   Press Ctrl+C to stop.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Server stopped.")
    finally:
        httpd.server_close()
