"""Manual HTML import: detect portal, extract listing, run full pipeline."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

import ollama
from bs4 import BeautifulSoup

from malta_housing.analysis.evaluator import evaluate_listing
from malta_housing.budget import is_out_of_budget
from malta_housing.common import (
    extract_title_and_text,
    infer_source_from_url,
    merge_staging,
)
from malta_housing.db.queries import get_listing_by_url
from malta_housing.db.store import init_db, save_evaluation, save_listings_to_db
from malta_housing.geo import is_gozo_listing, is_gozo_record
from malta_housing.models import ParsedListing, ScrapedListing, SourceType, utc_now_iso
from malta_housing.parsing.llm import MODEL_NAME, parse_staged_item
from malta_housing.scrapers.belair import parse_belair_html
from malta_housing.scrapers.propertymarket import _normalize_listing_url, parse_propertymarket_html

ALL_SOURCES: frozenset[SourceType] = frozenset(
    {
        "maltapark",
        "ownersbest",
        "djar",
        "propertymarket",
        "yitaku",
        "remax",
        "simonmamo",
        "belair",
    }
)

_PORTAL_HOST_MARKERS: tuple[tuple[str, SourceType], ...] = (
    ("propertymarket.com.mt", "propertymarket"),
    ("maltapark.com", "maltapark"),
    ("ownersbest.com.mt", "ownersbest"),
    ("djar.ai", "djar"),
    ("yitaku.com", "yitaku"),
    ("remax-malta.com", "remax"),
    ("simonmamo.com", "simonmamo"),
    ("belair.com.mt", "belair"),
)

_HTML_MARKERS: tuple[tuple[str, SourceType], ...] = (
    ("mylistingdetailstitle", "propertymarket"),
    ("singel_page_card", "belair"),
    ("/property/sm-", "simonmamo"),
    ("maltapark.com", "maltapark"),
    ("ownersbest.com.mt", "ownersbest"),
    ("djar.ai", "djar"),
    ("propertymarket.com.mt", "propertymarket"),
    ("remax-malta.com", "remax"),
    ("yitaku.com", "yitaku"),
    ("belair.com.mt", "belair"),
)

_LISTING_PATH_RE = re.compile(
    r"/(?:property|view|listings|properties)/",
    re.I,
)

ProgressCallback = Callable[[str, str], None]


def _portal_from_url_string(url: str) -> SourceType | None:
    lowered = url.lower()
    for needle, source in _PORTAL_HOST_MARKERS:
        if needle in lowered:
            return source
    return infer_source_from_url(url)


def _looks_like_listing_href(href: str) -> bool:
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return False
    return bool(_LISTING_PATH_RE.search(href))


def extract_url_from_html(html: str) -> str | None:
    """Try canonical, og:url, then listing links on known portal hosts."""
    soup = BeautifulSoup(html, "html.parser")

    og_url = soup.find("meta", property="og:url")
    if og_url and og_url.get("content"):
        url = str(og_url["content"]).strip()
        if url:
            return url

    canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
    if canonical and canonical.get("href"):
        url = str(canonical["href"]).strip()
        if url:
            return url

    for tag in soup.find_all("a", href=True):
        href = str(tag["href"]).strip()
        if not _looks_like_listing_href(href):
            continue
        for needle, _ in _PORTAL_HOST_MARKERS:
            if needle in href.lower():
                if href.startswith("http"):
                    return href.split("#", 1)[0].split("?", 1)[0]
                break

    base_tag = soup.find("base", href=True)
    base_href = str(base_tag["href"]).strip() if base_tag else None
    for tag in soup.find_all("a", href=True):
        href = str(tag["href"]).strip()
        if not _looks_like_listing_href(href):
            continue
        if base_href:
            full = urljoin(base_href, href)
            if _portal_from_url_string(full):
                return full.split("#", 1)[0].split("?", 1)[0]

    return None


def detect_source_from_html(html: str) -> SourceType | None:
    lowered = html.lower()
    for marker, source in _HTML_MARKERS:
        if marker in lowered:
            return source
    return None


def detect_source_with_llm(html: str) -> SourceType | None:
    snippet = html[:8000]
    prompt = (
        "Identify which Malta property portal this HTML page source came from.\n"
        "Return JSON only: {\"source\": \"maltapark|ownersbest|djar|propertymarket|"
        "yitaku|remax|simonmamo|belair|null\"}\n\n"
        f"HTML snippet:\n{snippet}"
    )
    try:
        response = ollama.chat(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            format="json",
        )
        data = json.loads(response["message"]["content"])
        source = data.get("source")
        if isinstance(source, str) and source in ALL_SOURCES:
            return source
    except Exception:
        pass
    return None


def detect_source(html: str, url: str | None = None) -> SourceType | None:
    if url:
        inferred = infer_source_from_url(url)
        if inferred:
            return inferred
    from_html = detect_source_from_html(html)
    if from_html:
        return from_html
    return detect_source_with_llm(html)


def _normalize_url_for_source(url: str, source: SourceType) -> str:
    if source == "propertymarket":
        normalized = _normalize_listing_url(url)
        if normalized:
            return normalized
    return url.split("#", 1)[0].split("?", 1)[0]


def _generic_html_listing(html: str, url: str, source: SourceType) -> ScrapedListing:
    title, raw_text = extract_title_and_text(html)
    if title == "Brak tytułu":
        title = f"{source} Property"
    if not raw_text.strip():
        raise ValueError("Could not extract text from HTML")
    return ScrapedListing(
        url=url,
        title=title,
        raw_text=raw_text,
        source=source,
        scraped_at=utc_now_iso(),
    )


def listing_from_html(source: SourceType, html: str, url: str) -> ScrapedListing:
    url = _normalize_url_for_source(url, source)
    if source == "propertymarket":
        listing = parse_propertymarket_html(html, url)
    elif source == "belair":
        listing = parse_belair_html(html, url)
    else:
        listing = _generic_html_listing(html, url, source)
    if not listing.raw_text.strip():
        raise ValueError("Parser did not extract listing text from HTML")
    return listing


def run_manual_pipeline(
    html: str,
    url: str | None = None,
    *,
    force_eval: bool = True,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    def step(name: str, message: str) -> None:
        if on_progress:
            on_progress(name, message)

    step("detecting", "Detecting portal…")

    resolved_url = (url or "").strip() or extract_url_from_html(html)
    if not resolved_url:
        raise ValueError(
            "Nie można ustalić URL ogłoszenia — podaj URL lub wklej pełny HTML z meta canonical/og:url."
        )

    source = detect_source(html, resolved_url)
    if not source:
        raise ValueError(
            "Nie znaleziono odpowiedniego parsera dla tego HTML. "
            "Obsługiwane portale: maltapark, ownersbest, djar, propertymarket, "
            "yitaku, remax, simonmamo, belair."
        )

    resolved_url = _normalize_url_for_source(resolved_url, source)

    step("extracting", f"Extracting with {source} parser…")
    scraped = listing_from_html(source, html, resolved_url)

    if is_gozo_listing(title=scraped.title, url=scraped.url, raw_text=scraped.raw_text):
        return {
            "status": "skipped",
            "step": "skipped",
            "message": "Skipped: Gozo listing",
            "reason": "Gozo listing",
            "url": scraped.url,
            "source": source,
        }

    step("staging", "Saving to staging…")
    merge_staging([scraped])
    staged_item = scraped.model_dump()

    step("parsing", "Parsing with Ollama…")
    parsed_dict = parse_staged_item(staged_item, force=True, html=html)

    if is_gozo_record(parsed_dict):
        return {
            "status": "skipped",
            "step": "skipped",
            "message": "Skipped: Gozo listing",
            "reason": "Gozo listing",
            "url": scraped.url,
            "source": source,
        }
    if is_out_of_budget(parsed_dict):
        return {
            "status": "skipped",
            "step": "skipped",
            "message": "Skipped: out of budget (€100k–€400k)",
            "reason": "Out of budget (€100k–€400k)",
            "url": scraped.url,
            "source": source,
        }

    step("db", "Saving to database…")
    init_db()
    save_listings_to_db([parsed_dict])

    listing_id: int | None = None
    listing_row = get_listing_by_url(scraped.url)
    if listing_row:
        listing_id = listing_row.get("id")

    if force_eval:
        step("evaluating", "AI investment evaluation…")
        parsed_listing = ParsedListing(**parsed_dict)
        evaluation = evaluate_listing(parsed_listing, staged_item["raw_text"])
        save_evaluation(scraped.url, evaluation)
        listing_row = get_listing_by_url(scraped.url)
        if listing_row:
            listing_id = listing_row.get("id")

    return {
        "status": "done",
        "step": "done",
        "message": "Import complete",
        "url": scraped.url,
        "source": source,
        "listing_id": listing_id,
    }
