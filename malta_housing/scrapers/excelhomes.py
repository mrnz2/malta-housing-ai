"""Excel Homes scraper — EAP JSON API merged into scraped_listings.json."""

from __future__ import annotations

import json
import random
import re
import time
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from malta_housing.common import HttpClient, extract_title_and_text, load_hidden_urls, merge_staging
from malta_housing.geo import is_gozo_listing
from malta_housing.models import ScrapedListing, utc_now_iso

BASE_URL = "https://excel.com.mt"
API_BASE = "https://api.estateagencyplatform.com/excel"
SOURCE = "excelhomes"
PAGE_SIZE = 12

# Matches the public buy search: sale, mainland regions, residential types, max €400k.
# Regions 1/3/4/5 = Central/North/South/Sliema Area (2 is Gozo).
# Types: apartment, character house, penthouse, maisonette, terraced house,
# townhouse, villa, development block (not-by-group IDs).
SEARCH_QUERY: dict[str, str] = {
    "limit": str(PAGE_SIZE),
    "market-type": "sale",
    "ref": "",
    "property-type": "1-2-3-22-25-26-27-32",
    "localities": "",
    "regions": "1-3-4-5",
    "price-from": "",
    "price-to": "400000",
    "sort": "",
    "not-by-group": "1",
}

API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE_URL}/properties/",
    "Origin": BASE_URL,
}

_PROPERTY_HREF_RE = re.compile(r"/property/([A-Za-z0-9-]+)/?", re.I)
_EMPTY_TEXT = frozenset({"", "n/a", "none", "null", '""', '"\\""'})


def _search_url(page_num: int) -> str:
    params = {**SEARCH_QUERY, "page": str(page_num)}
    return f"{API_BASE}/api/v1/properties/search?{urlencode(params)}"


def _detail_url(ref: str) -> str:
    return f"{API_BASE}/api/v1/properties/{ref}"


def _listing_url(slug: str) -> str:
    slug = slug.strip().strip("/")
    return f"{BASE_URL}/property/{slug}/"


def _normalize_listing_url(href: str) -> str | None:
    href = href.strip()
    match = _PROPERTY_HREF_RE.search(href)
    if not match:
        return None
    full_url = href if href.startswith("http") else BASE_URL + href
    return _listing_url(match.group(1))


def _nested_text(value: Any, *keys: str) -> str:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    if current is None:
        return ""
    if isinstance(current, dict):
        for nested_key in ("description", "locality_name", "name", "label"):
            nested = current.get(nested_key)
            if nested:
                return str(nested).strip()
        return ""
    return str(current).strip()


def _locality_for(item: dict[str, Any]) -> str:
    return _nested_text(item, "locality_details")


def _property_type_for(item: dict[str, Any]) -> str:
    return _nested_text(item, "propertyType_details")


def _region_for(item: dict[str, Any]) -> str:
    return _nested_text(item, "region_details")


def _title_for(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    if title:
        return title
    prop_type = _property_type_for(item) or "Property"
    locality = _locality_for(item)
    if locality:
        return f"{prop_type} in {locality}"
    ref = item.get("ref") or item.get("webRef") or ""
    return f"Excel Homes {ref}".strip() or "Excel Homes Property"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in _EMPTY_TEXT:
        return ""
    return text


def _fmt_price(item: dict[str, Any]) -> str:
    if item.get("priceOnRequest") in (1, "1", True):
        return "P.O.R."
    raw = item.get("price")
    if raw in (None, "", "0", "0.00"):
        return "n/a"
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return str(raw)
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


def _features_text(features: Any) -> str:
    if not features:
        return ""
    if isinstance(features, str):
        return _clean_text(features)
    if not isinstance(features, list):
        return _clean_text(features)
    parts: list[str] = []
    for feature in features:
        if isinstance(feature, str):
            cleaned = _clean_text(feature)
            if cleaned:
                parts.append(cleaned)
        elif isinstance(feature, dict):
            name = (
                feature.get("name")
                or feature.get("description")
                or feature.get("label")
                or feature.get("feature")
            )
            cleaned = _clean_text(name)
            if cleaned:
                parts.append(cleaned)
    return ", ".join(parts)


def _raw_text_for(item: dict[str, Any], url: str) -> str:
    """Build LLM-friendly text from the EAP property payload."""
    consultant = item.get("consultant") if isinstance(item.get("consultant"), dict) else {}
    agent = _clean_text(consultant.get("fullNameField")) if consultant else ""
    garage = _clean_text(item.get("garage"))
    specs = _clean_text(item.get("specifications"))
    included = _clean_text(item.get("itemsIncludedInPrice"))
    features = _features_text(item.get("features"))
    description = (
        _clean_text(item.get("longDescription"))
        or _clean_text(item.get("description"))
        or _clean_text(item.get("hotPropertyTitle"))
    )
    market = _clean_text(item.get("marketType")) or "Sale"
    lines = [
        f"Title: {_title_for(item)}",
        f"URL: {url}",
        f"Reference: {item.get('ref') or item.get('webRef') or 'n/a'}",
        f"Price: {_fmt_price(item)}",
        f"Locality: {_locality_for(item) or 'n/a'}",
        f"Region: {_region_for(item) or 'n/a'}",
        f"Property type: {_property_type_for(item) or 'n/a'}",
        f"Contract: {market}",
        f"Bedrooms: {item.get('bedrooms') if item.get('bedrooms') is not None else 'n/a'}",
        f"Bathrooms: {item.get('bathrooms') if item.get('bathrooms') is not None else 'n/a'}",
        "Seller type: AGENT",
        f"Seller name: {agent or 'Excel Homes'}",
    ]
    area = _fmt_area(item.get("area"))
    internal = _fmt_area(item.get("internal_area"))
    external = _fmt_area(item.get("external_area"))
    plot = _fmt_area(item.get("plot_area"))
    if area:
        lines.append(f"Area m2: {area}")
    if internal:
        lines.append(f"Internal area m2: {internal}")
    if external:
        lines.append(f"External area m2: {external}")
    if plot:
        lines.append(f"Plot area m2: {plot}")
    if garage:
        lines.append(f"Garage: {garage}")
    if item.get("soleAgents"):
        lines.append("Sole agency: yes")
    if features:
        lines.append(f"Features: {features}")
    if specs:
        lines.append(f"Specifications: {specs}")
    if included:
        lines.append(f"Included in price: {included}")
    if description:
        lines.extend(["", "Description:", description])
    return "\n".join(lines).strip()


def item_to_scraped(item: dict[str, Any]) -> ScrapedListing | None:
    slug = str(item.get("slug") or "").strip()
    ref = str(item.get("ref") or item.get("webRef") or "").strip()
    if not slug and not ref:
        return None
    url = _listing_url(slug) if slug else f"{BASE_URL}/property/{ref}/"
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


def parse_excelhomes_html(html: str, url: str) -> ScrapedListing:
    """Extract a staging listing from an Excel Homes property page (paste-import)."""
    normalized = _normalize_listing_url(url) or url.split("#", 1)[0].split("?", 1)[0]
    soup = BeautifulSoup(html, "html.parser")
    og_title = _meta_content(soup, "og:title")
    title = og_title.split("|", 1)[0].strip() if og_title else ""
    if not title:
        extracted, _ = extract_title_and_text(html)
        title = extracted.split("|", 1)[0].strip() if extracted and extracted != "Brak tytułu" else ""
    if not title:
        title = "Excel Homes Property"

    og_description = _meta_content(soup, "og:description")
    canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
    page_url = str(canonical["href"]).strip() if canonical and canonical.get("href") else normalized
    page_url = _normalize_listing_url(page_url) or page_url

    lines = [
        f"Title: {title}",
        f"URL: {page_url}",
        "Seller type: AGENT",
        "Seller name: Excel Homes",
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


def _parse_json(response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except json.JSONDecodeError as e:
        print(f"   └─ Niepoprawny JSON: {e}")
        return None
    if not isinstance(payload, dict):
        print(f"   └─ Nieoczekiwany kształt odpowiedzi: {type(payload)}")
        return None
    return payload


def fetch_search_page(client: HttpClient, page_num: int) -> dict[str, Any] | None:
    """GET one EAP search page; returns parsed JSON or None on failure."""
    url = _search_url(page_num)
    print(f"🔎 [Excel Homes] API strona {page_num}: {url}")
    try:
        response = client.get(url)
    except Exception as e:
        print(f"   └─ Błąd HTTP strony {page_num}: {e}")
        return None

    payload = _parse_json(response)
    if payload is None:
        return None

    items = payload.get("data")
    if not isinstance(items, list):
        print("   └─ Brak tablicy 'data' — stop.")
        return None

    paginator = payload.get("paginator") if isinstance(payload.get("paginator"), dict) else {}
    total_pages = paginator.get("total_pages")
    print(
        f"   └─ {len(items)} ofert"
        + (f" (strona {page_num}/{total_pages})" if total_pages is not None else "")
        + "."
    )
    return payload


def fetch_property(client: HttpClient, ref: str) -> dict[str, Any] | None:
    """GET full property JSON by portal ref."""
    try:
        response = client.get(_detail_url(ref))
    except Exception as e:
        print(f"   └─ Błąd pobierania ref {ref}: {e}")
        return None
    payload = _parse_json(response)
    if payload is None:
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(payload, dict) and payload.get("ref"):
        return payload
    print(f"   └─ Brak obiektu oferty dla ref {ref}.")
    return None


def run_excelhomes_scraper(max_pages: int = 3) -> list[dict]:
    """Fetch up to max_pages from Excel Homes EAP API, merge into staging."""
    client = HttpClient(headers=API_HEADERS, timeout=20.0)
    scraped_data: list[ScrapedListing] = []
    skipped_gozo = 0
    skipped_hidden = 0
    hidden_urls = load_hidden_urls()
    seen_refs: set[str] = set()

    print(f"🚀 Rozpoczynam pobieranie z Excel Homes (strony 1-{max_pages})...\n")

    pending: list[tuple[str, str]] = []  # (ref, listing url)

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
            ref = str(item.get("ref") or item.get("webRef") or "").strip()
            slug = str(item.get("slug") or "").strip()
            if not ref or ref in seen_refs:
                continue
            seen_refs.add(ref)
            url = _listing_url(slug) if slug else f"{BASE_URL}/property/{ref}/"
            if url in hidden_urls:
                skipped_hidden += 1
                print(f"   └─ Pominięto (ukryte): {url}")
                continue
            locality = _locality_for(item)
            title = _title_for(item)
            if is_gozo_listing(title=title, locality=locality, url=url):
                skipped_gozo += 1
                print(f"   └─ Pominięto Gozo: {title}")
                continue
            pending.append((ref, url))

        paginator = payload.get("paginator") if isinstance(payload.get("paginator"), dict) else {}
        try:
            total_pages = int(paginator.get("total_pages") or 0)
        except (TypeError, ValueError):
            total_pages = 0
        if total_pages and page >= total_pages:
            print("   └─ Osiągnięto last page — koniec.")
            break
        if page < max_pages:
            time.sleep(random.uniform(0.5, 1.2))

    print(f"\n📊 Łącznie zebrano {len(pending)} unikalnych ofert z Excel Homes.\n")

    for i, (ref, url) in enumerate(pending, 1):
        print(f"[{i}/{len(pending)}] Pobieranie opisu: {url}")
        detail = fetch_property(client, ref)
        if not detail:
            continue
        scraped = item_to_scraped(detail)
        if scraped is None:
            continue
        locality = _locality_for(detail)
        if is_gozo_listing(title=scraped.title, locality=locality, url=scraped.url):
            skipped_gozo += 1
            print("   └─ Pominięto (Gozo).")
            continue
        scraped_data.append(scraped)
        if i < len(pending):
            time.sleep(random.uniform(0.35, 0.9))

    merged = merge_staging(scraped_data)
    print(
        f"\n✅ Zakończono! Pobrano {len(scraped_data)} ogłoszeń"
        f" (pominięto {skipped_gozo} Gozo, {skipped_hidden} ukryte); "
        f"staging ma teraz {len(merged)} unikalnych URL-i."
    )
    print("👉 Możesz teraz uruchomić: python -m malta_housing parse")
    return [item.model_dump() for item in scraped_data]


if __name__ == "__main__":
    run_excelhomes_scraper(max_pages=3)
