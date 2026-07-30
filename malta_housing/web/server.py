"""Local HTTP server for browsing the SQLite listings database."""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from malta_housing.common import configure_stdio
from malta_housing.db import queries
from malta_housing.db.store import init_db, set_listing_fav, set_listing_hidden, set_listing_notes, set_listing_ready
from malta_housing.manual_import import run_manual_pipeline
from malta_housing.paths import DB_PATH, PACKAGE_ROOT

STATIC_DIR = PACKAGE_ROOT / "web" / "static"
MAX_MANUAL_IMPORT_BYTES = 3 * 1024 * 1024


@dataclass
class ImportJob:
    id: str
    status: str = "queued"
    step: str = "queued"
    message: str = "Queued"
    error: str | None = None
    url: str | None = None
    source: str | None = None
    listing_id: int | None = None


_jobs: dict[str, ImportJob] = {}
_jobs_lock = threading.Lock()


def _create_import_job() -> ImportJob:
    job = ImportJob(id=str(uuid.uuid4()))
    with _jobs_lock:
        _jobs[job.id] = job
    return job


def _get_import_job(job_id: str) -> ImportJob | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def _update_import_job(job_id: str, **kwargs: Any) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        for key, value in kwargs.items():
            setattr(job, key, value)


def _job_to_dict(job: ImportJob) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "status": job.status,
        "step": job.step,
        "message": job.message,
        "error": job.error,
        "url": job.url,
        "source": job.source,
        "listing_id": job.listing_id,
    }


def _run_import_job(job_id: str, html: str, url: str | None) -> None:
    _update_import_job(job_id, status="running", step="queued", message="Starting…")

    def on_progress(step: str, message: str) -> None:
        _update_import_job(job_id, status="running", step=step, message=message)

    try:
        result = run_manual_pipeline(html, url, on_progress=on_progress)
        status = result.get("status", "done")
        if status == "skipped":
            _update_import_job(
                job_id,
                status="skipped",
                step=result.get("step", "skipped"),
                message=result.get("message", "Skipped"),
                url=result.get("url"),
                source=result.get("source"),
                listing_id=result.get("listing_id"),
            )
        else:
            _update_import_job(
                job_id,
                status="done",
                step="done",
                message=result.get("message", "Import complete"),
                url=result.get("url"),
                source=result.get("source"),
                listing_id=result.get("listing_id"),
            )
    except Exception as exc:
        _update_import_job(
            job_id,
            status="failed",
            step="failed",
            message="Import failed",
            error=str(exc),
        )


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
                fav_only=_parse_bool(qs.get("fav_only")) is True,
                sort=(qs.get("sort") or ["ai_score_desc"])[0],
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

        match_job = re.fullmatch(r"/api/jobs/([a-f0-9-]+)", path)
        if match_job:
            job = _get_import_job(match_job.group(1))
            if job is None:
                self._send(*_json_bytes({"error": "Job not found"}, 404))
                return
            self._send(*_json_bytes(_job_to_dict(job)))
            return

        self._send(*_json_bytes({"error": "Not found"}, 404))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/api/manual-import":
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_MANUAL_IMPORT_BYTES:
                self._send(
                    *_json_bytes(
                        {"error": f"HTML too large (max {MAX_MANUAL_IMPORT_BYTES} bytes)"},
                        413,
                    )
                )
                return
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send(*_json_bytes({"error": "Invalid JSON"}, 400))
                return
            html = body.get("html")
            if not isinstance(html, str) or not html.strip():
                self._send(*_json_bytes({"error": "Missing non-empty 'html' string"}, 400))
                return
            url = body.get("url")
            if url is not None and not isinstance(url, str):
                self._send(*_json_bytes({"error": "'url' must be a string"}, 400))
                return
            job = _create_import_job()
            thread = threading.Thread(
                target=_run_import_job,
                args=(job.id, html, url.strip() if isinstance(url, str) and url.strip() else None),
                daemon=True,
            )
            thread.start()
            self._send(*_json_bytes(_job_to_dict(job), 202))
            return

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

        match_ready = re.fullmatch(r"/api/listings/(\d+)/ready", path)
        if match_ready:
            listing_id = int(match_ready.group(1))
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send(*_json_bytes({"error": "Invalid JSON"}, 400))
                return
            if "ready" not in body:
                self._send(*_json_bytes({"error": "Missing 'ready'"}, 400))
                return
            ready = body["ready"]
            if ready is not None and not isinstance(ready, bool):
                self._send(*_json_bytes({"error": "'ready' must be a boolean or null"}, 400))
                return
            if not set_listing_ready(listing_id, ready):
                self._send(*_json_bytes({"error": "Listing not found"}, 404))
                return
            listing = queries.get_listing(listing_id)
            self._send(*_json_bytes(listing or {"id": listing_id, "ready": ready}))
            return

        match_fav = re.fullmatch(r"/api/listings/(\d+)/fav", path)
        if match_fav:
            listing_id = int(match_fav.group(1))
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send(*_json_bytes({"error": "Invalid JSON"}, 400))
                return
            if "fav" not in body:
                self._send(*_json_bytes({"error": "Missing 'fav' boolean"}, 400))
                return
            fav = bool(body["fav"])
            if not set_listing_fav(listing_id, fav):
                self._send(*_json_bytes({"error": "Listing not found"}, 404))
                return
            listing = queries.get_listing(listing_id)
            self._send(*_json_bytes(listing or {"id": listing_id, "is_fav": fav}))
            return

        match_notes = re.fullmatch(r"/api/listings/(\d+)/notes", path)
        if match_notes:
            listing_id = int(match_notes.group(1))
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send(*_json_bytes({"error": "Invalid JSON"}, 400))
                return
            if "notes" not in body:
                self._send(*_json_bytes({"error": "Missing 'notes'"}, 400))
                return
            notes = body["notes"]
            if notes is not None and not isinstance(notes, str):
                self._send(*_json_bytes({"error": "'notes' must be a string or null"}, 400))
                return
            if not set_listing_notes(listing_id, notes):
                self._send(*_json_bytes({"error": "Listing not found"}, 404))
                return
            listing = queries.get_listing(listing_id)
            self._send(*_json_bytes(listing or {"id": listing_id, "notes": notes}))
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
