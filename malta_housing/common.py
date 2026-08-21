"""Shared HTTP client, staging I/O, and HTML helpers."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urlparse

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
    "infer_source_from_url",
    "resolve_source",
    "extract_title_and_text",
    "load_json_list",
    "load_hidden_urls",
    "merge_staging",
    "purge_hidden_from_json",
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

RETRYABLE_STATUS = {403, 429, 500, 502, 503, 504}

_SG_REDIRECT_RE = re.compile(r'content="0;([^"]+)"', re.I)
_SG_CHALLENGE_RE = re.compile(r'const\s+sgchallenge="([^"]+)"')
_SG_SUBMIT_RE = re.compile(r'const\s+sgsubmit_url="([^"]+)"')


def _solve_sg_pow(challenge_str: str, max_attempts: int = 20_000_000) -> tuple[str, int, int]:
    """Solve SiteGround SHA1 proof-of-work; returns (base64 solution, ms, nonce)."""
    complexity = int(challenge_str.split(":", 1)[0])
    started = time.time()
    challenge_bytes = challenge_str.encode("utf-8")
    for nonce in range(max_attempts):
        candidate = challenge_bytes + str(nonce).encode("utf-8")
        digest = hashlib.sha1(candidate).digest()
        first_word = int.from_bytes(digest[:4], "big")
        if (first_word >> (32 - complexity)) == 0:
            elapsed_ms = int((time.time() - started) * 1000)
            return base64.b64encode(candidate).decode("ascii"), elapsed_ms, nonce
    raise RuntimeError(f"SiteGround PoW not solved within {max_attempts} attempts")


def _is_sg_challenge(response) -> bool:
    if getattr(response, "status_code", None) != 202:
        return False
    headers = {str(k).lower(): v for k, v in response.headers.items()}
    if headers.get("sg-captcha") == "challenge":
        return True
    text = getattr(response, "text", "") or ""
    return "sgcaptcha" in text.lower()


def _session_has_cookie(session, name: str) -> bool:
    try:
        if session.cookies.get(name):
            return True
    except Exception:
        pass
    try:
        return any(getattr(c, "name", None) == name for c in session.cookies)
    except Exception:
        return name in str(session.cookies)


class HttpClient:
    """Session-backed HTTP GET with retries on 429/5xx.

    Optional ``impersonate`` (e.g. ``\"chrome124\"``) uses curl_cffi to mimic a
    real browser TLS fingerprint — needed for portals that 403 plain requests.

    SiteGround Anti-Bot (HTTP 202 + sg-captcha) is solved automatically via
    JavaScript proof-of-work and the resulting ``_I_`` cookie is kept on the session.
    """

    def __init__(
        self,
        headers: dict[str, str] | None = None,
        timeout: float = 12.0,
        max_retries: int = 3,
        backoff_base: float = 1.5,
        impersonate: str | None = None,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.impersonate = impersonate
        self._sg_solving = False

        if impersonate:
            from curl_cffi import requests as cf_requests

            self.session = cf_requests.Session(impersonate=impersonate)
        else:
            self.session = requests.Session()
        self.session.headers.update(headers or DEFAULT_HEADERS)

    def clear_cookies(self) -> None:
        """Drop session cookies (e.g. before a fresh SiteGround PoW)."""
        self.session.cookies.clear()

    def get(self, url: str, *, referer: str | None = None) -> requests.Response:
        last_error: Exception | None = None
        req_headers = {"Referer": referer} if referer else None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.get(
                    url, timeout=self.timeout, headers=req_headers
                )
                if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                    wait = self.backoff_base * attempt
                    if response.status_code == 403:
                        wait = max(wait, 5.0)
                    retry_after = response.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        wait = max(wait, float(retry_after))
                    time.sleep(wait)
                    continue
                if _is_sg_challenge(response):
                    if self._sg_solving:
                        raise requests.HTTPError(
                            f"202 Accepted (bot challenge, already solving) for {url}",
                            response=response,
                        )
                    print("🧩 SiteGround bot challenge — solving PoW…")
                    self._solve_siteground_challenge(url, response)
                    response = self.session.get(
                        url, timeout=self.timeout, headers=req_headers
                    )
                    if _is_sg_challenge(response):
                        raise requests.HTTPError(
                            f"202 Accepted (bot challenge persists) for {url}",
                            response=response,
                        )
                    print("   └─ Challenge solved; continuing.")
                response.raise_for_status()
                return response
            except Exception as exc:
                last_error = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status not in RETRYABLE_STATUS and status < 500:
                    break
                if attempt >= self.max_retries:
                    break
                time.sleep(self.backoff_base * attempt)
        raise requests.RequestException(
            f"Failed GET {url} after {self.max_retries} attempts: {last_error}"
        )

    def _solve_siteground_challenge(self, original_url: str, challenge_response) -> None:
        """Fetch PoW challenge, solve it, and store the ``_I_`` cookie on the session."""
        self._sg_solving = True
        try:
            text = challenge_response.text or ""
            match = _SG_REDIRECT_RE.search(text)
            if not match:
                raise RuntimeError("SiteGround 202 response missing challenge redirect")

            parsed = urlparse(original_url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            challenge_path = match.group(1)
            challenge_url = (
                challenge_path
                if challenge_path.startswith("http")
                else base + challenge_path
            )

            page = self.session.get(
                challenge_url,
                timeout=max(self.timeout, 20.0),
                headers={"Referer": original_url},
            )
            page_text = page.text or ""
            challenge_match = _SG_CHALLENGE_RE.search(page_text)
            submit_match = _SG_SUBMIT_RE.search(page_text)
            if not challenge_match or not submit_match:
                raise RuntimeError("SiteGround challenge page missing sgchallenge/sgsubmit_url")

            challenge = challenge_match.group(1)
            submit_path = submit_match.group(1)
            solution, elapsed_ms, nonce = _solve_sg_pow(challenge)
            print(f"   └─ PoW solved in {elapsed_ms}ms (nonce={nonce}).")

            sep = "&" if "?" in submit_path else "?"
            submit_url = (
                f"{base}{submit_path}{sep}sol={quote(solution, safe='')}"
                f"&s={elapsed_ms}:{nonce}"
            )
            self.session.get(
                submit_url,
                timeout=max(self.timeout, 20.0),
                allow_redirects=True,
                headers={"Referer": challenge_url},
            )
            if not _session_has_cookie(self.session, "_I_"):
                raise RuntimeError("SiteGround submit did not set _I_ cookie")
        finally:
            self._sg_solving = False


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
    hidden_urls = load_hidden_urls()
    existing = {
        item["url"]: item
        for item in load_json_list(path)
        if "url" in item and item["url"] not in hidden_urls
    }
    for item in new_items:
        payload = item.model_dump() if isinstance(item, ScrapedListing) else dict(item)
        if "source" not in payload:
            raise ValueError("Staging items must include 'source'")
        if payload["url"] in hidden_urls:
            continue
        existing[payload["url"]] = payload
    merged = list(existing.values())
    save_json_list(path, merged)
    return merged


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    ensure_data_dir()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_hidden_urls() -> set[str]:
    """URLs marked hidden in SQLite — skip scrape, parse, and rank."""
    from malta_housing.db.store import get_hidden_urls

    return get_hidden_urls()


def purge_hidden_from_json(path: Path) -> int:
    """Remove hidden listing URLs from a JSON list file. Returns count removed."""
    hidden_urls = load_hidden_urls()
    if not hidden_urls:
        return 0
    items = load_json_list(path)
    if not items:
        return 0
    kept = [item for item in items if item.get("url") not in hidden_urls]
    removed = len(items) - len(kept)
    if removed:
        save_json_list(path, kept)
    return removed


def ensure_source(value: str) -> SourceType:
    if value not in {
        "maltapark",
        "ownersbest",
        "djar",
        "propertymarket",
        "yitaku",
        "remax",
        "simonmamo",
        "belair",
        "re316",
        "franksalt",
        "sensar",
    }:
        raise ValueError(f"Unknown source: {value}")
    return value  # type: ignore[return-value]


_URL_SOURCE_HOSTS: tuple[tuple[str, SourceType], ...] = (
    ("maltapark.com", "maltapark"),
    ("ownersbest.com.mt", "ownersbest"),
    ("djar.ai", "djar"),
    ("propertymarket.com.mt", "propertymarket"),
    ("yitaku.com", "yitaku"),
    ("remax-malta.com", "remax"),
    ("simonmamo.com", "simonmamo"),
    ("belair.com.mt", "belair"),
    ("316.com.mt", "re316"),
    ("franksalt.com.mt", "franksalt"),
    ("sensaramalta.com", "sensar"),
)


def infer_source_from_url(url: str | None) -> SourceType | None:
    """Map a listing URL to its scraper source portal."""
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for needle, source in _URL_SOURCE_HOSTS:
        if host == needle or host.endswith("." + needle):
            return source
    lowered = url.lower()
    for needle, source in _URL_SOURCE_HOSTS:
        if needle in lowered:
            return source
    return None


def resolve_source(source: str | None, url: str | None = None) -> SourceType | None:
    """Prefer an explicit source; otherwise infer from the listing URL."""
    if isinstance(source, str):
        cleaned = source.strip().lower()
        if cleaned in {
            "maltapark",
            "ownersbest",
            "djar",
            "propertymarket",
            "yitaku",
            "remax",
            "simonmamo",
            "belair",
            "re316",
            "franksalt",
            "sensar",
        }:
            return cleaned  # type: ignore[return-value]
    return infer_source_from_url(url)
