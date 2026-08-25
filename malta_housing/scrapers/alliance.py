"""Alliance Real Estate scraper — WordPress admin-ajax JSON merged into staging."""

from __future__ import annotations

import json
import random
import re
import time
from typing import Any

from bs4 import BeautifulSoup

from malta_housing.common import HttpClient, extract_title_and_text, load_hidden_urls, merge_staging
from malta_housing.geo import is_gozo_listing
from malta_housing.models import ScrapedListing, utc_now_iso

BASE_URL = "https://alliance.mt"
AJAX_URL = f"{BASE_URL}/wp-admin/admin-ajax.php"
SOURCE = "alliance"
PAGE_SIZE = 6  # fixed by Alliance fetch_property endpoint
MAX_PRICE = 400000

# Residential sale filters matching the public property-search UI.
CATEGORIES = ("Apartments", "BLK", "HSE", "Maisonettes", "Penthouses")
SUBCATEGORIES = (
    "APT",
    "APTBLK",
    "ADU",
    "FLT",
    "GFA",
    "HAP",
    "ABP",
    "SFL",
    "TFA",
    "APTT",
    "BOA",
    "MSN1F",
    "MSN2F",
    "MSN3F",
    "MD",
    "MDU",
    "MEGF",
    "MSNG",
    "MLFT",
    "MSBAS",
    "MSD",
    "MSOL",
    "MST",
    "PTH",
    "PDU",
    "PHT",
    "TERB",
    "TEH",
)

API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/property-search/?isSale=true",
    "X-Requested-With": "XMLHttpRequest",
}

_PROPERTY_HREF_RE = re.compile(r"/property/([A-Za-z0-9-]+)/?", re.I)
_EMPTY_TEXT = frozenset({"", "n/a", "none", "null", '""', '"\\""'})


def _listing_url(slug: str) -> str:
    slug = slug.strip().strip("/")
    return f"{BASE_URL}/property/{slug}/"


def _normalize_listing_url(href: str) -> str | None:
    href = href.strip()
    match = _PROPERTY_HREF_RE.search(href)
    if not match:
        return None
    return _listing_url(match.group(1))


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in _EMPTY_TEXT:
        return ""
    return text


def _locality_for(item: dict[str, Any]) -> str:
    locality = item.get("locality") if isinstance(item.get("locality"), dict) else {}
    return _clean_text(locality.get("title"))


def _region_for(item: dict[str, Any]) -> str:
    locality = item.get("locality") if isinstance(item.get("locality"), dict) else {}
    region = locality.get("region") if isinstance(locality.get("region"), dict) else {}
    return _clean_text(region.get("title"))


def _category_for(item: dict[str, Any]) -> str:
    category = item.get("category") if isinstance(item.get("category"), dict) else {}
    return _clean_text(category.get("title"))


def _is_gozo_item(item: dict[str, Any], *, title: str, url: str) -> bool:
    if _region_for(item).lower() == "gozo":
        return True
    return is_gozo_listing(title=title, locality=_locality_for(item), url=url)


def _title_for(item: dict[str, Any]) -> str:
    prop_type = _category_for(item) or "Property"
    locality = _locality_for(item)
    beds = item.get("numberOfBedrooms")
    bed_prefix = ""
    try:
        if beds is not None and int(beds) > 0:
            bed_prefix = f"{int(beds)}-bedroom "
    except (TypeError, ValueError):
        pass
    if locality:
        return f"{bed_prefix}{prop_type} in {locality}".strip()
    ref = _clean_text(item.get("referenceNumber"))
    return f"Alliance {ref}".strip() or "Alliance Property"


def _fmt_price(value: Any) -> str:
    if value in (None, "", "0", "0.00"):
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number <= 0:
        return "n/a"
    return f"€ {int(round(number)):,}"


def _fmt_area(value: Any) -> str:
    text = _clean_text(value)
    if not text or text in {"0", "0.00", "0.0"}:
        return ""
    try:
        number = float(text)
    except ValueError:
        return text
    if number <= 0:
        return ""
    if number == int(number):
        return str(int(number))
    return f"{number:g}"


def _tags_text(tags: Any) -> str:
    if not isinstance(tags, list):
        return ""
    parts: list[str] = []
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        tag_type = _clean_text(tag.get("type")).upper()
        # Skip internal CRM tags that are not useful for the LLM.
        if tag_type in {"CUSTOM_SLUG", "GUID_AGENTIDENTIFIER"}:
            continue
        name = _clean_text(tag.get("name"))
        if name:
            parts.append(name)
    return ", ".join(parts)


def _agent_name(item: dict[str, Any]) -> str:
    agent = item.get("agent") if isinstance(item.get("agent"), dict) else {}
    name = " ".join(
        part for part in (_clean_text(agent.get("name")), _clean_text(agent.get("surname"))) if part
    )
    return name or "Alliance Real Estate"


def _search_form(page_num: int) -> list[tuple[str, str]]:
    data: list[tuple[str, str]] = [
        ("action", "fetch_property"),
        ("page", str(page_num)),
        ("params[sortKey]", "soleagency"),
        ("params[sortDirection]", "DESC"),
        ("params[isSale]", "true"),
        ("params[maxPrice]", str(MAX_PRICE)),
    ]
    for category in CATEGORIES:
        data.append(("params[categoryIdentifiers][]", category))
    for subcategory in SUBCATEGORIES:
        data.append(("params[subCategoryIdentifiers][]", subcategory))
    return data


def _raw_text_for(item: dict[str, Any], url: str) -> str:
    """Build LLM-friendly text from an Alliance fetch_property payload."""
    description = _clean_text(item.get("writeUp"))
    tags = _tags_text(item.get("tags"))
    lines = [
        f"Title: {_title_for(item)}",
        f"URL: {url}",
        f"Reference: {_clean_text(item.get('referenceNumber')) or 'n/a'}",
        f"Price: {_fmt_price(item.get('price'))}",
        f"Locality: {_locality_for(item) or 'n/a'}",
        f"Region: {_region_for(item) or 'n/a'}",
        f"Property type: {_category_for(item) or 'n/a'}",
        "Contract: Sale",
        f"Bedrooms: {item.get('numberOfBedrooms') if item.get('numberOfBedrooms') is not None else 'n/a'}",
        f"Bathrooms: {item.get('numberOfBathrooms') if item.get('numberOfBathrooms') is not None else 'n/a'}",
        "Seller type: AGENT",
        f"Seller name: {_agent_name(item)}",
    ]
    area = _fmt_area(item.get("totalArea"))
    plot = _fmt_area(item.get("plotArea"))
    if area:
        lines.append(f"Area m2: {area}")
    if plot:
        lines.append(f"Plot area m2: {plot}")
    car_spaces = item.get("numberOfCarSpaces")
    try:
        if car_spaces is not None and int(car_spaces) > 0:
            lines.append(f"Car spaces: {int(car_spaces)}")
    except (TypeError, ValueError):
        pass
    if item.get("isSoleAgency") in (True, "true", "1", 1):
        lines.append("Sole agency: yes")
    if tags:
        lines.append(f"Features: {tags}")
    if description:
        lines.extend(["", "Description:", description])
    return "\n".join(lines).strip()


def item_to_scraped(item: dict[str, Any]) -> ScrapedListing | None:
    slug = _clean_text(item.get("slug"))
    ref = _clean_text(item.get("referenceNumber"))
    if not slug and not ref:
        return None
    url = _listing_url(slug) if slug else f"{BASE_URL}/property/{ref.lower()}/"
    return ScrapedListing(
        url=url,
        title=_title_for(item),
        raw_text=_raw_text_for(item, url),
        source=SOURCE,
        scraped_at=utc_now_iso(),
    )


def _meta_content(soup: BeautifulSoup, property_name: str) -> str:
    tag = soup.find("meta", property=property_name) or soup.find("meta", attrs={"name": property_name})
    if not tag:
        return ""
    return str(tag.get("content") or "").strip()


def parse_alliance_html(html: str, url: str) -> ScrapedListing:
    """Extract a staging listing from an Alliance property page (paste-import)."""
    normalized = _normalize_listing_url(url) or url.split("#", 1)[0].split("?", 1)[0]
    soup = BeautifulSoup(html, "html.parser")
    og_title = _meta_content(soup, "og:title")
    title = og_title.split("|", 1)[0].strip() if og_title else ""
    if title.lower().endswith(" - alliance real estate"):
        title = title[: -len(" - Alliance Real Estate")].strip()
    if not title:
        extracted, _ = extract_title_and_text(html)
        title = extracted.split("|", 1)[0].strip() if extracted and extracted != "Brak tytułu" else ""
    if not title:
        title = "Alliance Property"

    og_description = _meta_content(soup, "og:description")
    canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
    page_url = str(canonical["href"]).strip() if canonical and canonical.get("href") else normalized
    page_url = _normalize_listing_url(page_url) or page_url

    lines = [
        f"Title: {title}",
        f"URL: {page_url}",
        "Seller type: AGENT",
        "Seller name: Alliance Real Estate",
    ]
    if og_description:
        lines.extend(["", "Description:", og_description])
    raw_text = "\n".join(lines).strip()
    if not og_description:
        _, fallback = extract_title_and_text(html)
        if fallback.strip():
            raw_text = f"{raw_text}\n\n{fallback}".strip()

    return ScrapedListing(
        url=page_url,
        title=title,
        raw_text=raw_text,
        source=SOURCE,
        scraped_at=utc_now_iso(),
    )


def fetch_search_page(client: HttpClient, page_num: int) -> dict[str, Any] | None:
    """POST one Alliance search page; returns parsed JSON or None on failure."""
    print(f"🔎 [Alliance] API strona {page_num}")
    try:
        response = client.session.post(AJAX_URL, data=_search_form(page_num), timeout=30)
        response.raise_for_status()
    except Exception as e:
        print(f"   └─ Błąd HTTP strony {page_num}: {e}")
        return None

    try:
        payload = response.json()
    except json.JSONDecodeError as e:
        print(f"   └─ Niepoprawny JSON: {e}")
        return None

    if not isinstance(payload, dict):
        print(f"   └─ Nieoczekiwany kształt odpowiedzi: {type(payload)}")
        return None

    items = payload.get("data")
    if not isinstance(items, list):
        print("   └─ Brak tablicy 'data' — stop.")
        return None

    count = payload.get("count")
    print(
        f"   └─ {len(items)} ofert"
        + (f" (łącznie ~{count})" if count is not None else "")
        + "."
    )
    return payload


def run_alliance_scraper(max_pages: int = 3) -> list[dict]:
    """Fetch up to max_pages from Alliance AJAX API, merge into staging."""
    client = HttpClient(headers=API_HEADERS, timeout=30.0)
    scraped_data: list[ScrapedListing] = []
    skipped_gozo = 0
    skipped_hidden = 0
    hidden_urls = load_hidden_urls()
    seen_refs: set[str] = set()

    print(f"🚀 Rozpoczynam pobieranie z Alliance (strony 1-{max_pages})...\n")

    for page in range(1, max_pages + 1):
        payload = fetch_search_page(client, page)
        if payload is None:
            break

        items = payload.get("data") or []
        if not items:
            print("   └─ Pusta strona — koniec paginacji.")
            break

        for item in items:
            if not isinstance(item, dict):
                continue
            ref = _clean_text(item.get("referenceNumber")) or _clean_text(item.get("id"))
            if not ref or ref in seen_refs:
                continue
            seen_refs.add(ref)

            scraped = item_to_scraped(item)
            if scraped is None:
                continue
            if scraped.url in hidden_urls:
                skipped_hidden += 1
                print(f"   └─ Pominięto (ukryte): {scraped.url}")
                continue
            if _is_gozo_item(item, title=scraped.title, url=scraped.url):
                skipped_gozo += 1
                print(f"   └─ Pominięto Gozo: {scraped.title}")
                continue
            scraped_data.append(scraped)

        total = payload.get("count")
        try:
            total_pages = (int(total) + PAGE_SIZE - 1) // PAGE_SIZE if total is not None else 0
        except (TypeError, ValueError):
            total_pages = 0
        if total_pages and page >= total_pages:
            print("   └─ Osiągnięto last page — koniec.")
            break
        if page < max_pages:
            time.sleep(random.uniform(0.5, 1.2))

    merged = merge_staging(scraped_data)
    print(
        f"\n✅ Zakończono! Pobrano {len(scraped_data)} ogłoszeń"
        f" (pominięto {skipped_gozo} Gozo, {skipped_hidden} ukryte); "
        f"staging ma teraz {len(merged)} unikalnych URL-i."
    )
    print("👉 Możesz teraz uruchomić: python -m malta_housing parse")
    return [item.model_dump() for item in scraped_data]


if __name__ == "__main__":
    run_alliance_scraper(max_pages=3)
