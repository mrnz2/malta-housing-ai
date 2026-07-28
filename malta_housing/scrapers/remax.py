"""RE/MAX Malta scraper — JSON API listings merged into scraped_listings.json."""

from __future__ import annotations

import json
import random
import time
from typing import Any
from urllib.parse import urlencode

from malta_housing.common import HttpClient, merge_staging
from malta_housing.geo import is_gozo_listing
from malta_housing.models import ScrapedListing, utc_now_iso

BASE_URL = "https://remax-malta.com"
API_URL = f"{BASE_URL}/api/properties"
SOURCE = "remax"
PAGE_SIZE = 40

DEFAULT_QUERY: dict[str, str] = {
    "Residential": "true",
    "Commercial": "false",
    "ForSale": "true",
    "ForRent": "false",
    "PriceFrom": "100000",
    "PriceTo": "400000",
    "SelectedPropertyTypes": "2,21,17,22,24,32,23,104,14,35,7,15,5,4",
    "Take": str(PAGE_SIZE),
}

API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE_URL}/",
}

def _page_url(page_num: int) -> str:
    params = {**DEFAULT_QUERY, "page": str(page_num)}
    return f"{API_URL}?{urlencode(params)}"


def _listing_url(item: dict[str, Any]) -> str:
    mls = (item.get("MLS") or "").strip()
    if mls:
        return f"{BASE_URL}/listings/{mls}"
    item_id = item.get("Id")
    return f"{BASE_URL}/listings/{item_id}"


def _title_for(item: dict[str, Any]) -> str:
    prop_type = (item.get("PropertyType") or "Property").strip()
    town = (item.get("Town") or "").strip()
    if town:
        return f"{prop_type} in {town}"
    return prop_type or f"RE/MAX listing {item.get('Id', '')}".strip()


def _price_int(item: dict[str, Any]) -> int | None:
    raw = item.get("Price")
    if raw is None:
        return None
    try:
        return int(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _raw_text_for(item: dict[str, Any], url: str) -> str:
    """Build LLM-friendly text from API fields (no extra HTML fetch)."""
    price = _price_int(item)
    price_txt = f"€ {price:,}" if price is not None else (item.get("PriceFormatted") or "n/a")
    coords = item.get("Coordinates") or {}
    lat = coords.get("lat")
    lon = coords.get("lon")
    coord_txt = f"{lat}, {lon}" if lat is not None and lon is not None else "n/a"

    lines = [
        f"Title: {_title_for(item)}",
        f"URL: {url}",
        f"Listing ref (MLS): {item.get('MLS') or 'n/a'}",
        f"Price: {price_txt}",
        f"Locality: {item.get('Town') or 'n/a'}",
        f"Zone: {item.get('Zone') or 'n/a'}",
        f"Region: {item.get('Province') or 'n/a'}",
        f"Property type: {item.get('PropertyType') or 'n/a'}",
        f"Contract: {item.get('TransactionType') or 'n/a'}",
        f"Bedrooms: {item.get('TotalBedrooms') if item.get('TotalBedrooms') is not None else 'n/a'}",
        f"Bathrooms: {item.get('TotalBathrooms') if item.get('TotalBathrooms') is not None else 'n/a'}",
        f"Total rooms: {item.get('TotalRooms') if item.get('TotalRooms') is not None else 'n/a'}",
        f"Area m2: {item.get('TotalSqm') or 'n/a'}",
        f"Internal area m2: {item.get('TotalIntArea') or 'n/a'}",
        f"External area m2: {item.get('TotalExtArea') or 'n/a'}",
        f"Status: {item.get('Status') or 'n/a'}",
        f"Coordinates: {coord_txt}",
        "",
        "Description:",
        str(item.get("Description") or "").strip(),
    ]
    return "\n".join(lines).strip()


def item_to_scraped(item: dict[str, Any]) -> ScrapedListing | None:
    item_id = item.get("Id")
    if item_id is None:
        return None
    url = _listing_url(item)
    return ScrapedListing(
        url=url,
        title=_title_for(item),
        raw_text=_raw_text_for(item, url),
        source=SOURCE,
        scraped_at=utc_now_iso(),
    )


def fetch_page(client: HttpClient, page_num: int) -> dict[str, Any] | None:
    """GET one API page; returns parsed JSON or None on failure."""
    url = _page_url(page_num)
    print(f"🔎 [RE/MAX] API strona {page_num}: {url}")
    try:
        response = client.get(url)
    except Exception as e:
        print(f"   └─ Błąd HTTP strony {page_num}: {e}")
        return None

    if response.status_code != 200:
        print(f"   └─ Status {response.status_code} — stop.")
        return None

    try:
        payload = response.json()
    except json.JSONDecodeError as e:
        print(f"   └─ Niepoprawny JSON na stronie {page_num}: {e}")
        return None

    if not isinstance(payload, dict):
        print(f"   └─ Nieoczekiwany kształt odpowiedzi: {type(payload)}")
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        print("   └─ Brak obiektu 'data' — stop.")
        return None

    items = data.get("Properties")
    if not isinstance(items, list):
        print("   └─ Brak tablicy 'Properties' — stop.")
        return None

    total = data.get("TotalSearchResults")
    print(
        f"   └─ {len(items)} ofert"
        + (f" (łącznie w wyszukiwaniu: {total})" if total is not None else "")
        + "."
    )
    return data


def run_remax_scraper(max_pages: int = 3) -> list[dict]:
    """Fetch up to max_pages from RE/MAX Malta API, merge into staging."""
    client = HttpClient(headers=API_HEADERS, timeout=20.0)
    scraped_data: list[ScrapedListing] = []
    skipped_gozo = 0
    seen_ids: set[Any] = set()

    print(f"🚀 Rozpoczynam pobieranie z RE/MAX Malta API (strony 1-{max_pages})...\n")

    for page in range(1, max_pages + 1):
        data = fetch_page(client, page)
        if data is None:
            break

        items = data.get("Properties") or []
        if not items:
            print("   └─ Pusta strona — koniec paginacji.")
            break

        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("Id")
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            scraped = item_to_scraped(item)
            if scraped is None:
                continue
            locality = item.get("Town") or ""
            if is_gozo_listing(title=scraped.title, locality=locality, url=scraped.url):
                skipped_gozo += 1
                print(f"   └─ Pominięto Gozo: {scraped.title}")
                continue
            scraped_data.append(scraped)

        if len(items) < PAGE_SIZE:
            print("   └─ Mniej niż Take — ostatnia strona.")
            break

        if page < max_pages:
            time.sleep(random.uniform(0.5, 1.2))

    merged = merge_staging(scraped_data)
    print(
        f"\n✅ Zakończono! Pobrano {len(scraped_data)} ogłoszeń"
        f" (pominięto {skipped_gozo} Gozo); "
        f"staging ma teraz {len(merged)} unikalnych URL-i."
    )
    print("👉 Możesz teraz uruchomić: python -m malta_housing parse")
    return [item.model_dump() for item in scraped_data]


if __name__ == "__main__":
    run_remax_scraper(max_pages=3)
