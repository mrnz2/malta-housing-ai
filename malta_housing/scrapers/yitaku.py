"""Yitaku scraper — JSON API listings merged into scraped_listings.json."""

from __future__ import annotations

import csv
import json
import random
import time
from typing import Any
from urllib.parse import urlencode

from malta_housing.common import HttpClient, merge_staging, save_json_list
from malta_housing.geo import is_gozo_listing
from malta_housing.models import ScrapedListing, utc_now_iso
from malta_housing.paths import DATA_DIR, ensure_data_dir

BASE_URL = "https://www.yitaku.com"
API_URL = f"{BASE_URL}/api/properties"
SOURCE = "yitaku"

DEFAULT_QUERY: dict[str, str] = {
    "contract": "For Sale",
    "category": "Residential",
    "region": "Malta",
    "min_price": "100000",
    "max_price": "400000",
}

API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

RAW_DUMP_PATH = DATA_DIR / "yitaku_properties.json"
CSV_DUMP_PATH = DATA_DIR / "yitaku_properties.csv"


def _page_url(page_num: int) -> str:
    params = {**DEFAULT_QUERY, "page": str(page_num)}
    return f"{API_URL}?{urlencode(params)}"


def _listing_url(item_id: Any) -> str:
    return f"{BASE_URL}/properties/{item_id}"


def _title_for(item: dict[str, Any]) -> str:
    subtype = (item.get("property_sub_type") or item.get("property_type") or "Property").strip()
    locality = (item.get("locality_name") or "").strip()
    if locality:
        return f"{subtype} in {locality}"
    return subtype or f"Yitaku listing {item.get('id', '')}".strip()


def _raw_text_for(item: dict[str, Any], url: str) -> str:
    """Build LLM-friendly text from API fields (no extra HTML fetch)."""
    price = item.get("Price")
    price_txt = f"€ {price:,}" if isinstance(price, (int, float)) else (str(price) if price else "n/a")
    features = item.get("other_features") or []
    if isinstance(features, list):
        features_txt = ", ".join(str(f) for f in features if f)
    else:
        features_txt = str(features)

    lines = [
        f"Title: {_title_for(item)}",
        f"URL: {url}",
        f"Price: {price_txt}",
        f"Locality: {item.get('locality_name') or 'n/a'}",
        f"Property type: {item.get('property_sub_type') or item.get('property_type') or 'n/a'}",
        f"Contract: {item.get('contract_type') or 'n/a'}",
        f"Bedrooms: {item.get('no_of_bedrooms_label') or 'n/a'}",
        f"Bathrooms: {item.get('no_of_bathrooms_label') or 'n/a'}",
        f"Area m2: {item.get('Total_area_in_m2') or 'n/a'}",
        f"Seller type: {item.get('seller_type') or 'n/a'}",
        f"Seller name: {item.get('seller_name') or 'n/a'}",
        f"Features: {features_txt or 'n/a'}",
        "",
        "Description:",
        str(item.get("property_description") or "").strip(),
    ]
    return "\n".join(lines).strip()


def item_to_scraped(item: dict[str, Any]) -> ScrapedListing | None:
    item_id = item.get("id")
    if item_id is None:
        return None
    url = _listing_url(item_id)
    title = _title_for(item)
    return ScrapedListing(
        url=url,
        title=title,
        raw_text=_raw_text_for(item, url),
        source=SOURCE,
        scraped_at=utc_now_iso(),
    )


def extract_summary_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten API items to key columns for optional CSV export."""
    rows: list[dict[str, Any]] = []
    for item in items:
        item_id = item.get("id")
        if item_id is None:
            continue
        rows.append(
            {
                "id": item_id,
                "title": _title_for(item),
                "price_eur": item.get("Price"),
                "locality": item.get("locality_name"),
                "region": "Malta",
                "bedrooms": item.get("no_of_bedrooms_label"),
                "area_m2": item.get("Total_area_in_m2"),
                "url": _listing_url(item_id),
            }
        )
    return rows


def save_summary_csv(items: list[dict[str, Any]], path=CSV_DUMP_PATH) -> None:
    ensure_data_dir()
    rows = extract_summary_rows(items)
    fieldnames = [
        "id",
        "title",
        "price_eur",
        "locality",
        "region",
        "bedrooms",
        "area_m2",
        "url",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"📄 Zapisano CSV ({len(rows)} wierszy): {path}")


def fetch_page(client: HttpClient, page_num: int) -> dict[str, Any] | None:
    """GET one API page; returns parsed JSON or None on failure / empty."""
    url = _page_url(page_num)
    print(f"🔎 [Yitaku] API strona {page_num}: {url}")
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

    items = payload.get("items")
    if not isinstance(items, list):
        print("   └─ Brak tablicy 'items' — stop.")
        return None

    cur = payload.get("curPage", page_num)
    total_pages = payload.get("pageTotal")
    print(
        f"   └─ {len(items)} ofert"
        + (f" (strona {cur}/{total_pages})" if total_pages is not None else "")
        + "."
    )
    return payload


def run_yitaku_scraper(max_pages: int = 3, *, write_csv: bool = True) -> list[dict]:
    """Fetch up to max_pages from Yitaku API, merge into staging."""
    client = HttpClient(headers=API_HEADERS, timeout=20.0)
    all_raw: list[dict[str, Any]] = []
    scraped_data: list[ScrapedListing] = []
    skipped_gozo = 0
    seen_ids: set[Any] = set()

    print(f"🚀 Rozpoczynam pobieranie z Yitaku API (strony 1-{max_pages})...\n")

    for page in range(1, max_pages + 1):
        payload = fetch_page(client, page)
        if payload is None:
            break

        items = payload.get("items") or []
        if not items:
            print("   └─ Pusta strona — koniec paginacji.")
            break

        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            all_raw.append(item)

            scraped = item_to_scraped(item)
            if scraped is None:
                continue
            locality = item.get("locality_name") or ""
            if is_gozo_listing(title=scraped.title, locality=locality, url=scraped.url):
                skipped_gozo += 1
                print(f"   └─ Pominięto Gozo: {scraped.title}")
                continue
            scraped_data.append(scraped)

        cur = int(payload.get("curPage") or page)
        last = payload.get("pageTotal")
        if last is not None:
            try:
                if cur >= int(last):
                    print("   └─ Osiągnięto last page — koniec.")
                    break
            except (TypeError, ValueError):
                pass

        if page < max_pages:
            time.sleep(random.uniform(0.5, 1.5))

    ensure_data_dir()
    save_json_list(RAW_DUMP_PATH, all_raw)
    print(f"💾 Surowy dump API: {RAW_DUMP_PATH} ({len(all_raw)} pozycji)")
    if write_csv:
        save_summary_csv(all_raw)

    merged = merge_staging(scraped_data)
    print(
        f"\n✅ Zakończono! Pobrano {len(scraped_data)} ogłoszeń"
        f" (pominięto {skipped_gozo} Gozo); "
        f"staging ma teraz {len(merged)} unikalnych URL-i."
    )
    print("👉 Możesz teraz uruchomić: python -m malta_housing parse")
    return [item.model_dump() for item in scraped_data]


if __name__ == "__main__":
    run_yitaku_scraper(max_pages=3)
