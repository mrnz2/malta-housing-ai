"""Dhalia Real Estate scraper — remindAPI JSON merged into scraped_listings.json."""

from __future__ import annotations

import json
import random
import re
import time
from typing import Any

from malta_housing.common import HttpClient, load_hidden_urls, merge_staging, load_json_list, save_json_list
from malta_housing.paths import STAGING_PATH
from malta_housing.geo import is_gozo_listing
from malta_housing.models import ScrapedListing, utc_now_iso

BASE_URL = "https://www.dhalia.com"
API_BASE = f"{BASE_URL}/api"
SEARCH_URL = f"{API_BASE}/remindAPI.svc/rest/propertySearchREST"
COUNT_URL = f"{API_BASE}/remindAPI.svc/rest/propertySearchCountREST"
DETAIL_URL = f"{API_BASE}/remindAPI.svc/rest/getPropertyByRefREST"
_DETAIL_RETRIES = 5
SOURCE = "dhalia"
PAGE_SIZE = 15
SORT_ORDER = "UpdDate DESC, Ref DESC"
LISTING_TYPE = "For Sale"

# Mainland Malta localities from the public buy search (Gozo excluded).
LOCALITIES = (
    "Attard,Bahar ic-Caghaq,Bahrija,Balzan,Bidnija,Birguma,Bir-id-Deheb,Birkirkara,Birzebbuga,"
    "Blata il-Bajda,Bugibba,Burmarrad,Buskett,Cospicua,Dingli,Fgura,Fleur de Lys,Ghajn Tuffieha,"
    "Floriana,Ghaxaq,Gharghur,Gudja,Gwardamangia,Gzira,Halfar,Hamrun,High Ridge,Kalafrana,Kalkara,"
    "Kappara,Kirkop,Kuncizzjoni,Landrijiet,Lija,L-Iklin,Luqa,Luqa-Industrial,Madliena,Maghtab,"
    "Manikata,Marfa,Marsa,Marsa-Industrial,Marsascala,Marsaxlokk,Mdina,Mellieha,Mensija,Mgarr,"
    "Mizieb,Mosta,Mqabba,Mriehel,Mriehel-Industrial,Msida,Mtahleb,Mtarfa,Naxxar,Paola,Paceville,"
    "Pembroke,Pieta,Qawra,Qormi,Qormi-Industrial,Qrendi,Rabat,Safi,Salina,San Gwann,"
    "San Pawl Tat-Targa,Santa Lucia,Santa Venera,Senglea,Siggiewi,Sliema,St Andrews,St Julians,"
    "St Paul's Bay,Swatar,Swieqi,Tal-Handaq,Ta'Giorni,Ta'L-ibrag,Tarxien,Ta'Xbiex,The Gardens,"
    "The Village,Valletta,Vittoriosa,Wardija,Xwieki,Xghajra,Xemxija,Zabbar,Zebbiegh,Zurrieq,Zejtun,Zebbug"
)

PROPERTY_TYPES = (
    "Apartment,Holiday Apartment,Holiday Villa,House of Character,Maisonette,Palazzo,Penthouse,"
    "Terraced House,Townhouse,Villa"
)

API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE_URL}/search",
    "Origin": BASE_URL,
}

_REF_RE = re.compile(r"(P\d{6,})", re.I)
_CANONICAL_URL_RE = re.compile(r"/(?:buy|rent)/[^/]+/[^/]+/(P\d{6,})", re.I)


def _path_slug(value: str, default: str) -> str:
    """Match Dhalia frontend: lower-case and replace whitespace with hyphens."""
    text = (value or "").strip().lower()
    if not text:
        return default
    return re.sub(r"\s+", "-", text)


def listing_url(item: dict[str, Any]) -> str:
    """Build canonical property URL, e.g. /buy/pieta/penthouse/P000252654."""
    ref = _clean_text(item.get("Ref"))
    if not ref:
        raise ValueError("Property ref is required for Dhalia listing URL")
    prop_type = _path_slug(_clean_text(item.get("Type")), "property")
    locality = _path_slug(_clean_text(item.get("Location")), "malta")
    status = _clean_text(item.get("SStatus")).lower()
    segment = "buy" if status == "for sale" else "rent"
    return f"{BASE_URL}/{segment}/{locality}/{prop_type}/{ref}"


def normalize_listing_url(url: str, item: dict[str, Any] | None = None) -> str | None:
    match = _REF_RE.search(url or "")
    if not match:
        return None
    ref = match.group(1).upper()
    if item:
        return listing_url({**item, "Ref": ref})
    canonical = _CANONICAL_URL_RE.search(url or "")
    if canonical and canonical.group(1).upper() == ref:
        return url.split("?", 1)[0].split("#", 1)[0]
    return None


def extract_ref(url: str) -> str | None:
    match = _REF_RE.search(url or "")
    return match.group(1).upper() if match else None


def is_canonical_url(url: str) -> bool:
    return _CANONICAL_URL_RE.search(url or "") is not None


def fetch_property_by_ref(
    client: HttpClient,
    ref: str,
    *,
    cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Fetch full property JSON by portal ref (POST remindAPI)."""
    ref = ref.strip().upper()
    if not ref:
        return None
    if cache is not None and ref in cache:
        return cache[ref]

    last_error: Exception | None = None
    for attempt in range(_DETAIL_RETRIES):
        try:
            response = client.session.post(
                f"{DETAIL_URL}?propertyRef={ref}",
                timeout=30,
            )
            if response.status_code == 429:
                time.sleep((2**attempt) + random.uniform(0.4, 1.2))
                continue
            response.raise_for_status()
            payload = _parse_api_payload(response.json())
            if isinstance(payload, dict):
                if cache is not None:
                    cache[ref] = payload
                return payload
            return None
        except Exception as e:
            last_error = e
            if attempt + 1 < _DETAIL_RETRIES:
                time.sleep((2**attempt) + random.uniform(0.3, 0.9))
    if last_error is not None:
        print(f"   └─ Błąd pobierania ref {ref}: {last_error}")
    return None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text in {"", "0", "0.0", "0.00", "null", "None"}:
        return ""
    return text


def _fmt_price(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return "n/a"
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    if number <= 0:
        return "n/a"
    return f"€ {int(round(number)):,}"


def _fmt_area(value: Any) -> str:
    text = _clean_text(value)
    if not text:
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


def _title_for(item: dict[str, Any]) -> str:
    ref = _clean_text(item.get("Ref"))
    prop_type = _clean_text(item.get("Type")) or "Property"
    locality = _clean_text(item.get("Location"))
    bedrooms = item.get("Bedrooms")
    if isinstance(bedrooms, (int, float)) and bedrooms > 0:
        bed_label = f"{int(bedrooms)} bedroom "
    else:
        bed_label = ""
    if locality:
        title = f"{bed_label}{prop_type} in {locality}"
    else:
        title = f"{bed_label}{prop_type}"
    if ref:
        title = f"{title} - Ref {ref}"
    return title.strip() or "Dhalia Property"


def _property_details_text(details: Any) -> str:
    if not isinstance(details, list) or not details:
        return ""
    parts: list[str] = []
    for row in details:
        if not isinstance(row, dict):
            continue
        label = _clean_text(row.get("Detail"))
        value = _clean_text(row.get("DetailValue"))
        measurement = _clean_text(row.get("Measurement"))
        if not label:
            continue
        if value and measurement:
            parts.append(f"{label}: {value} {measurement}")
        elif value:
            parts.append(f"{label}: {value}")
        else:
            parts.append(label)
    return "; ".join(parts)


def _parse_api_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        if "Object reference not set" in payload:
            return None
        return json.loads(payload)
    return payload


def _search_params(page_index: int) -> dict[str, str]:
    return {
        "localities": LOCALITIES,
        "priceMin": "",
        "priceMax": "400000",
        "pageIndex": str(page_index),
        "pagesize": str(PAGE_SIZE),
        "sortOrder": SORT_ORDER,
        "listingType": LISTING_TYPE,
        "propertyType": PROPERTY_TYPES,
        "bedrooms": "",
        "bathrooms": "",
        "garden": "",
        "views": "",
        "pool": "",
        "garage": "",
        "agent": "",
        "floorAreaMin": "",
        "floorAreaMax": "",
    }


def _raw_text_for(item: dict[str, Any], url: str) -> str:
    """Build LLM-friendly text from a remindAPI property payload."""
    ref = _clean_text(item.get("Ref"))
    description = _clean_text(item.get("SaleWriteup")) or _clean_text(item.get("LeaseWriteup"))
    details = _property_details_text(item.get("PropertyDetails"))
    ground_rent = _fmt_price(item.get("GRent"))
    ground_type = _clean_text(item.get("GRType"))
    lines = [
        f"Title: {_title_for(item)}",
        f"URL: {url}",
        f"Reference: {ref or 'n/a'}",
        f"Price: {_fmt_price(item.get('Price'))}",
        f"Locality: {_clean_text(item.get('Location')) or 'n/a'}",
        f"Town: {_clean_text(item.get('Town')) or 'n/a'}",
        f"Property type: {_clean_text(item.get('Type')) or 'n/a'}",
        f"Form: {_clean_text(item.get('Form')) or 'n/a'}",
        f"Status: {_clean_text(item.get('SStatus')) or LISTING_TYPE}",
        f"Bedrooms: {item.get('Bedrooms') if item.get('Bedrooms') is not None else 'n/a'}",
        f"Bathrooms: {item.get('Bathrooms') if item.get('Bathrooms') is not None else 'n/a'}",
        f"Floor: {_clean_text(item.get('Floor')) or 'n/a'}",
        "Seller type: AGENT",
        "Seller name: Dhalia Real Estate",
    ]
    for label, key in (
        ("Internal area m2", "IArea"),
        ("External area m2", "EArea"),
        ("Plot area m2", "PArea"),
    ):
        area = _fmt_area(item.get(key))
        if area:
            lines.append(f"{label}: {area}")
    floor_area = _fmt_area(item.get("FArea"))
    if floor_area:
        lines.append(f"Floor Area: {floor_area} sqm")
        lines.append(f"Area m2: {floor_area}")
    garage = _clean_text(item.get("Garage"))
    if garage:
        lines.append(f"Garage: {garage}")
    views = _clean_text(item.get("Views"))
    if views:
        lines.append(f"Views: {views}")
    if item.get("SoleAgency") == "Y":
        lines.append("Sole agency: yes")
    if ground_rent != "n/a":
        lines.append(f"Ground rent: {ground_rent}")
    if ground_type:
        lines.append(f"Ground rent type: {ground_type}")
    if details:
        lines.append(f"Property details: {details}")
    if description:
        lines.extend(["", "Description:", description])
    return "\n".join(lines).strip()


def item_to_scraped(item: dict[str, Any]) -> ScrapedListing | None:
    ref = _clean_text(item.get("Ref"))
    if not ref:
        return None
    url = listing_url(item)
    return ScrapedListing(
        url=url,
        title=_title_for(item),
        raw_text=_raw_text_for(item, url),
        source=SOURCE,
        scraped_at=utc_now_iso(),
    )


def fetch_search_page(client: HttpClient, page_num: int) -> list[dict[str, Any]] | None:
    """POST one search page; returns property dicts or None on failure."""
    params = _search_params(page_num)
    print(f"🔎 [Dhalia] API strona {page_num}")
    try:
        response = client.session.post(SEARCH_URL, params=params, timeout=30)
        response.raise_for_status()
    except Exception as e:
        print(f"   └─ Błąd HTTP strony {page_num}: {e}")
        return None

    try:
        payload = _parse_api_payload(response.json())
    except (json.JSONDecodeError, TypeError) as e:
        print(f"   └─ Niepoprawny JSON strony {page_num}: {e}")
        return None

    if payload is None:
        print(f"   └─ Pusta odpowiedź strony {page_num}.")
        return []
    if not isinstance(payload, list):
        print(f"   └─ Nieoczekiwany kształt odpowiedzi: {type(payload)}")
        return None

    print(f"   └─ {len(payload)} ofert.")
    return [row for row in payload if isinstance(row, dict)]


def fetch_total_count(client: HttpClient) -> int | None:
    try:
        response = client.session.post(COUNT_URL, params=_search_params(1), timeout=30)
        response.raise_for_status()
        payload = _parse_api_payload(response.json())
        if isinstance(payload, int):
            return payload
        if isinstance(payload, str) and payload.isdigit():
            return int(payload)
    except Exception:
        return None
    return None


def run_dhalia_scraper(max_pages: int = 3) -> list[dict]:
    """Fetch up to max_pages from Dhalia remindAPI, merge into staging."""
    client = HttpClient(headers=API_HEADERS, timeout=30.0)
    scraped_data: list[ScrapedListing] = []
    skipped_gozo = 0
    skipped_hidden = 0
    hidden_urls = load_hidden_urls()
    seen_refs: set[str] = set()
    api_cache: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, str]] = []  # (ref, url)

    total = fetch_total_count(client)
    if total is not None:
        print(f"🚀 Rozpoczynam pobieranie z Dhalia ({total} ofert w filtrze, strony 1-{max_pages})...\n")
    else:
        print(f"🚀 Rozpoczynam pobieranie z Dhalia (strony 1-{max_pages})...\n")

    for page in range(1, max_pages + 1):
        items = fetch_search_page(client, page)
        if items is None:
            break
        if not items:
            print("   └─ Pusta strona — koniec paginacji.")
            break

        for item in items:
            ref = _clean_text(item.get("Ref"))
            if not ref or ref in seen_refs:
                continue
            seen_refs.add(ref)
            url = listing_url(item)
            if url in hidden_urls:
                skipped_hidden += 1
                print(f"   └─ Pominięto (ukryte): {url}")
                continue
            locality = _clean_text(item.get("Location"))
            title = _title_for(item)
            if is_gozo_listing(title=title, locality=locality, url=url):
                skipped_gozo += 1
                print(f"   └─ Pominięto Gozo: {title}")
                continue
            pending.append((ref, url))

        if len(items) < PAGE_SIZE:
            print("   └─ Ostatnia strona — koniec.")
            break
        if page < max_pages:
            time.sleep(random.uniform(0.5, 1.2))

    print(f"\n📊 Łącznie zebrano {len(pending)} unikalnych ofert z Dhalia.\n")

    for i, (ref, url) in enumerate(pending, 1):
        print(f"[{i}/{len(pending)}] Pobieranie szczegółów: {url}")
        detail = fetch_property_by_ref(client, ref, cache=api_cache)
        if not detail:
            continue
        scraped = item_to_scraped(detail)
        if scraped is None:
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


def refresh_dhalia_staging_areas(*, dry_run: bool = False) -> dict[str, int]:
    """Re-fetch Dhalia detail API and patch Floor Area into staging raw_text."""
    staging = load_json_list(STAGING_PATH)
    if not staging:
        return {"total": 0, "updated": 0, "api_failed": 0, "skipped": 0}

    client = HttpClient(headers=API_HEADERS, timeout=30.0)
    api_cache: dict[str, dict[str, Any]] = {}
    stats = {"total": 0, "updated": 0, "api_failed": 0, "skipped": 0}
    dhalia_items = [
        item
        for item in staging
        if isinstance(item, dict)
        and (item.get("source") == SOURCE or "dhalia.com" in str(item.get("url", "")).lower())
    ]
    stats["total"] = len(dhalia_items)

    for index, item in enumerate(dhalia_items, 1):
        ref = extract_ref(str(item.get("url") or ""))
        if not ref:
            stats["skipped"] += 1
            continue
        detail = fetch_property_by_ref(client, ref, cache=api_cache)
        if not detail:
            stats["api_failed"] += 1
            print(f"[{index}/{len(dhalia_items)}] API brak danych: {ref}")
            continue
        url = str(item.get("url") or listing_url(detail))
        new_raw = _raw_text_for(detail, url)
        if item.get("raw_text") == new_raw:
            continue
        print(f"[{index}/{len(dhalia_items)}] Aktualizuję metraż: {ref}")
        if not dry_run:
            item["raw_text"] = new_raw
        stats["updated"] += 1
        if index < len(dhalia_items):
            time.sleep(random.uniform(0.35, 0.9))

    if not dry_run and stats["updated"]:
        save_json_list(STAGING_PATH, staging)
    return stats


if __name__ == "__main__":
    run_dhalia_scraper(max_pages=3)
