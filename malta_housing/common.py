"""Shared HTTP client, staging I/O, and HTML helpers."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup

from malta_housing.models import ScrapedListing, SourceType
from malta_housing.paths import (
    PARSE_FAILURES_PATH,
    PARSED_PATH,
    STAGING_PATH,
    ensure_data_dir,
)

# Re-export path constants for convenience
__all__ = [
    "HttpClient",
    "PARSE_FAILURES_PATH",
    "PARSED_PATH",
    "STAGING_PATH",
    "append_jsonl",
    "configure_stdio",
    "ensure_source",
    "extract_title_and_text",
    "load_json_list",
    "merge_staging",
    "save_json_list",
    "strip_noise_tags",
]


def configure_stdio() -> None:
    """Avoid UnicodeEncodeError on Windows consoles (cp1252) when printing emoji."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


configure_stdio()

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class HttpClient:
    """Session-backed HTTP GET with retries on 429/5xx."""

    def __init__(
        self,
        headers: dict[str, str] | None = None,
        timeout: float = 12.0,
        max_retries: int = 3,
        backoff_base: float = 1.5,
    ):
        self.session = requests.Session()
        self.session.headers.update(headers or DEFAULT_HEADERS)
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    def get(self, url: str) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout)
                if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                    wait = self.backoff_base * attempt
                    retry_after = response.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        wait = max(wait, float(retry_after))
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(self.backoff_base * attempt)
        raise requests.RequestException(
            f"Failed GET {url} after {self.max_retries} attempts: {last_error}"
        )


def strip_noise_tags(soup: BeautifulSoup) -> BeautifulSoup:
    """Remove non-content tags before extracting readable text."""
    for element in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        element.decompose()
    return soup


def extract_title_and_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("h1") or soup.find("h2") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "Brak tytułu"
    strip_noise_tags(soup)
    raw_text = soup.get_text(separator="\n", strip=True)
    return title, raw_text


def load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list in {path}")
    return data


def save_json_list(path: Path, items: list[dict[str, Any]]) -> None:
    ensure_data_dir()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)


def merge_staging(
    new_items: Iterable[dict[str, Any] | ScrapedListing],
    path: Path = STAGING_PATH,
) -> list[dict[str, Any]]:
    """Merge new scraped items into staging by URL (newer wins)."""
    existing = {item["url"]: item for item in load_json_list(path) if "url" in item}
    for item in new_items:
        payload = item.model_dump() if isinstance(item, ScrapedListing) else dict(item)
        if "source" not in payload:
            raise ValueError("Staging items must include 'source'")
        existing[payload["url"]] = payload
    merged = list(existing.values())
    save_json_list(path, merged)
    return merged


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    ensure_data_dir()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def ensure_source(value: str) -> SourceType:
    if value not in {"maltapark", "ownersbest", "djar"}:
        raise ValueError(f"Unknown source: {value}")
    return value  # type: ignore[return-value]
