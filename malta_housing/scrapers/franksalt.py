"""Frank Salt Real Estate scraper — AJAX listings API merged into scraped_listings.json."""

from __future__ import annotations

import random
import re
import time
from urllib.parse import quote

from bs4 import BeautifulSoup

from malta_housing.common import HttpClient, extract_title_and_text, load_hidden_urls, merge_staging
from malta_housing.geo import is_gozo_listing
from malta_housing.models import ScrapedListing, utc_now_iso

BASE_URL = "https://franksalt.com.mt"
SOURCE = "franksalt"
API_URL = f"{BASE_URL}/wp-json/fs/ajax/properties"
_DEFAULT_MAX_PAGES = 5

# Malta localities from portal search (excludes Gozo IDs used on the site).
_LOCALITY_IDS: tuple[int, ...] = (
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 22, 24,
    27, 29, 30, 31, 32, 33, 35, 36, 37, 38, 39, 42, 43, 44, 45, 46, 47, 48, 49,
    51, 52, 53, 54, 55, 56, 57, 58, 60, 61, 62, 63, 64, 65, 66, 69, 70, 71, 72,
    73, 74, 75, 76, 77, 78, 79, 81, 82, 83, 84, 86, 87, 89, 91, 93, 95, 147, 96,
    97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112,
    113, 114, 115, 116, 148, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126,
    127, 128, 129, 130, 131, 134, 135, 137, 139, 141, 142, 143, 145, 146,
)

# Residential property-type IDs from the portal buy search.
_PROPERTY_TYPE_IDS: tuple[int, ...] = (
    2, 3, 66, 49, 64, 45, 24, 48, 50, 28, 41, 75, 37, 40, 76, 31, 65, 68, 73,
    7, 19, 47, 44, 18, 11, 34, 46, 9,
)

# curl_cffi chrome124 sets browser headers; DEFAULT_HEADERS mismatch triggers CF 403.
HEADERS = {
    "Referer": f"{BASE_URL}/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

_PROPERTY_HREF_RE = re.compile(r"/property/([A-Za-z0-9]+)/?", re.I)
# Real listing refs are numeric (919688) or letter-prefix + digits (SG9268713) — not page/filter ids.
_PROPERTY_REF_RE = re.compile(r"^(?:[A-Za-z]{1,4})?\d{5,}$")
_LONG_DESC_RE = re.compile(
    r"\[longDescription\]\s*=>\s*(.*?)\n\s*\[specifications\]",
    re.S,
)
_LOCALITY_NAME_RE = re.compile(
    r"\[localityName\]\s*=>\s*([^\n\r]+)",
)
_PROPERTY_TYPE_NAME_RE = re.compile(
    r"\[propertyType_details\].*?\[description\]\s*=>\s*([^\n\r]+)",
    re.S,
)
_AREA_SQM_RE = re.compile(r"\[areaSqm\]\s*=>\s*([\d.]+)")
_INTERNAL_AREA_SQM_RE = re.compile(r"\[internalAreaSqm\]\s*=>\s*([\d.]+)")
_EXTERNAL_AREA_SQM_RE = re.compile(r"\[externalAreaSqm\]\s*=>\s*([\d.]+)")


def _search_filters() -> str:
    parts = ["mode=buy", "sort=recommend"]
    for locality_id in _LOCALITY_IDS:
        parts.append(f"localities[]={locality_id}")
    for type_id in _PROPERTY_TYPE_IDS:
        parts.append(f"properties-types[]={type_id}")
    parts.extend(
        [
            "priceFrom=",
            "priceTo=400000",
            "bathrooms=",
            "bedrooms=",
            "features=",
            "sole-agency=",
            "featured-property=false",
            "buyer-types=",
            "min-size=",
            "max-size=",
            "availableFrom=",
            "commercial=0",
        ]
    )
    return "&".join(parts)


def _api_page_url(page_num: int) -> str:
    return f"{API_URL}?page_number={page_num}&filters={quote(_search_filters(), safe='')}"


def _normalize_listing_url(href: str) -> str | None:
    href = href.strip()
    match = _PROPERTY_HREF_RE.search(href)
    if not match:
        return None
    ref = match.group(1)
    if not _PROPERTY_REF_RE.fullmatch(ref):
        return None
    return f"{BASE_URL}/property/{ref}/"


def _url_from_ref(ref: str) -> str | None:
    ref = ref.strip()
    if not _PROPERTY_REF_RE.fullmatch(ref):
        return None
    return f"{BASE_URL}/property/{ref}/"


def _warm_session(client: HttpClient) -> None:
    """Hit homepage first — direct /properties and API calls often 403 without it."""
    try:
        client.get(f"{BASE_URL}/")
    except Exception as e:
        print(f"   └─ Ostrzeżenie: warmup homepage nie udał się: {e}")


def get_item_links_from_page(client: HttpClient, page_num: int) -> list[str]:
    """Fetch one AJAX listings page and return unique property URLs."""
    url = _api_page_url(page_num)
    print(f"🔎 [Frank Salt] AJAX strona {page_num}")

    try:
        response = client.get(url, referer=f"{BASE_URL}/properties/")
    except Exception as e:
        print(f"   └─ Błąd podczas pobierania strony {page_num}: {e}")
        return []

    try:
        payload = response.json()
    except Exception as e:
        print(f"   └─ Niepoprawny JSON na stronie {page_num}: {e}")
        return []

    if not isinstance(payload, dict):
        print(f"   └─ Nieoczekiwany format odpowiedzi na stronie {page_num}.")
        return []

    fragments = payload.get("properties_in_html") or []
    if not isinstance(fragments, list):
        print(f"   └─ Brak HTML kart na stronie {page_num}.")
        return []

    links: set[str] = set()
    for fragment in fragments:
        if not isinstance(fragment, str):
            continue
        soup = BeautifulSoup(fragment, "html.parser")
        for el in soup.select("[data-property-ref]"):
            normalized = _url_from_ref(str(el.get("data-property-ref") or ""))
            if normalized:
                links.add(normalized)
        for el in soup.select(".fs-property-click[data-url]"):
            normalized = _normalize_listing_url(str(el.get("data-url") or ""))
            if normalized:
                links.add(normalized)
        for a_tag in soup.select("a[href*='/property/']"):
            normalized = _normalize_listing_url(a_tag["href"])
            if normalized:
                links.add(normalized)

    total = payload.get("results_count")
    pages = payload.get("number_of_pages")
    if total is not None and pages is not None:
        print(
            f"   └─ Znaleziono {len(links)} ogłoszeń "
            f"(wyniki: {total}, stron: {pages})."
        )
    else:
        print(f"   └─ Znaleziono {len(links)} ogłoszeń.")
    return sorted(links)


def _meta_content(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", property=prop)
    if tag and tag.get("content"):
        return str(tag["content"]).strip() or None
    return None


def _strip_html(value: str) -> str:
    return BeautifulSoup(value, "html.parser").get_text("\n", strip=True)


def _title_from_detail(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    og_title = _meta_content(soup, "og:title")
    if og_title:
        return re.sub(r"\s*-\s*\d+\s*-\s*Frank Salt.*$", "", og_title, flags=re.I).strip() or og_title
    heading = soup.find("h1")
    if heading:
        return heading.get_text(" ", strip=True)
    title, _ = extract_title_and_text(html)
    return title


def _reference_from_url(url: str) -> str | None:
    match = _PROPERTY_HREF_RE.search(url)
    return match.group(1) if match else None


def _feature_lines(soup: BeautifulSoup) -> list[str]:
    features: list[str] = []
    container = soup.select_one(".property-features-container")
    if not container:
        return features
    for item in container.select("li"):
        text = item.get_text(" ", strip=True)
        if text:
            features.append(re.sub(r"\s+", " ", text))
    return features


def _long_description(html: str) -> str | None:
    match = _LONG_DESC_RE.search(html)
    if not match:
        return None
    text = _strip_html(match.group(1))
    return text or None


def _dump_field(html: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(html)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def _dump_area_sqm(html: str, pattern: re.Pattern[str]) -> str | None:
    """Return area from PHP dump when present and > 0."""
    raw = _dump_field(html, pattern)
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    # Keep one decimal when needed (130.50 → 130.5), else int-like (122.00 → 122).
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def _raw_text_from_detail(html: str, url: str, title: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    price_el = soup.select_one(".currency-price-container")
    price = price_el.get_text(" ", strip=True) if price_el else None
    if price and not price.startswith("€"):
        price = f"€ {price}"
    reference = _reference_from_url(url)
    locality = _dump_field(html, _LOCALITY_NAME_RE)
    property_type = _dump_field(html, _PROPERTY_TYPE_NAME_RE)
    og_description = _meta_content(soup, "og:description")
    description = _long_description(html) or og_description
    subtitle = None
    for heading in soup.select("h2.elementor-heading-title"):
        text = heading.get_text(" ", strip=True)
        if " in " in text.lower() and len(text) < 120:
            subtitle = text
            break

    total_area = _dump_area_sqm(html, _AREA_SQM_RE)
    internal_area = _dump_area_sqm(html, _INTERNAL_AREA_SQM_RE)
    external_area = _dump_area_sqm(html, _EXTERNAL_AREA_SQM_RE)

    lines = [
        f"Title: {title}",
        f"URL: {url}",
        f"Reference No.: {reference or 'n/a'}",
        f"Price: {price or 'n/a'}",
        f"Locality: {locality or 'n/a'}",
        f"Property type: {property_type or 'n/a'}",
        f"Subtitle: {subtitle or 'n/a'}",
    ]
    if total_area:
        lines.append(f"Total area: {total_area} sqm")
    if internal_area:
        lines.append(f"Internal area: {internal_area} sqm")
    if external_area:
        lines.append(f"External area: {external_area} sqm")
    tags = soup.select_one(".property-tags-container")
    if tags:
        lines.append(f"Rooms: {tags.get_text(' ', strip=True)}")
    features = _feature_lines(soup)
    if features:
        lines.extend(["", "Property Features:"])
        lines.extend(features)
    if description:
        lines.extend(["", "Description:", description])
    return "\n".join(lines).strip()


def parse_franksalt_html(html: str, url: str) -> ScrapedListing:
    """Extract a staging listing from a Frank Salt property page."""
    normalized = _normalize_listing_url(url) or url
    title = _title_from_detail(html)
    if title == "Brak tytułu":
        title = "Frank Salt Property"

    raw_text = _raw_text_from_detail(html, normalized, title)
    if not raw_text.strip():
        _, raw_text = extract_title_and_text(html)

    return ScrapedListing(
        url=normalized,
        title=title,
        raw_text=raw_text,
        source=SOURCE,
        scraped_at=utc_now_iso(),
    )


def scrape_item_details(client: HttpClient, url: str) -> ScrapedListing | None:
    """Fetch a single Frank Salt property page."""
    try:
        response = client.get(url, referer=f"{BASE_URL}/properties/")
    except Exception as e:
        print(f"   └─ Błąd pobierania {url}: {e}")
        return None

    return parse_franksalt_html(response.text, url)


def run_franksalt_scraper(max_pages: int = _DEFAULT_MAX_PAGES) -> list[dict]:
    """Fetch up to max_pages from Frank Salt (default 5), merge into staging."""
    client = HttpClient(headers=HEADERS, impersonate="chrome124", timeout=25.0)
    all_item_urls: set[str] = set()

    print(f"🚀 Rozpoczynam pobieranie z Frank Salt (strony 1-{max_pages})...\n")
    _warm_session(client)

    for page in range(1, max_pages + 1):
        links = get_item_links_from_page(client, page)
        if not links:
            print("   └─ Pusta strona — koniec paginacji.")
            break
        all_item_urls.update(links)
        if page < max_pages:
            time.sleep(random.uniform(0.8, 1.6))

    print(f"\n📊 Łącznie zebrano {len(all_item_urls)} unikalnych ofert z Frank Salt.\n")

    scraped_data: list[ScrapedListing] = []
    skipped_gozo = 0
    skipped_hidden = 0
    hidden_urls = load_hidden_urls()
    for i, url in enumerate(sorted(all_item_urls), 1):
        if url in hidden_urls:
            skipped_hidden += 1
            print(f"[{i}/{len(all_item_urls)}] Pominięto (ukryte): {url}")
            continue
        print(f"[{i}/{len(all_item_urls)}] Pobieranie opisu: {url}")
        item_data = scrape_item_details(client, url)
        if item_data:
            if is_gozo_listing(title=item_data.title, url=item_data.url, raw_text=item_data.raw_text):
                skipped_gozo += 1
                print("   └─ Pominięto (Gozo).")
            else:
                scraped_data.append(item_data)
        time.sleep(random.uniform(1.0, 2.0))

    merged = merge_staging(scraped_data)
    print(
        f"\n✅ Zakończono! Pobrano {len(scraped_data)} ogłoszeń"
        f" (pominięto {skipped_gozo} Gozo, {skipped_hidden} ukryte); "
        f"staging ma teraz {len(merged)} unikalnych URL-i."
    )
    print("👉 Możesz teraz uruchomić: python -m malta_housing parse")
    return [item.model_dump() for item in scraped_data]


if __name__ == "__main__":
    run_franksalt_scraper(max_pages=_DEFAULT_MAX_PAGES)
