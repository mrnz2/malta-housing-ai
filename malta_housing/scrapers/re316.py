"""316 Real Estate scraper — merges results into scraped_listings.json."""

from __future__ import annotations

import random
import re
import time

from bs4 import BeautifulSoup

from malta_housing.common import HttpClient, extract_title_and_text, load_hidden_urls, merge_staging
from malta_housing.geo import is_gozo_listing
from malta_housing.models import ScrapedListing, utc_now_iso

BASE_URL = "https://316.com.mt"
SOURCE = "re316"

SEARCH_QUERY = (
    "type=SALE"
    "&categories%5B0%5D=1246&categories%5B1%5D=1252&categories%5B2%5D=1254"
    "&categories%5B3%5D=1273&categories%5B4%5D=1281"
    "&subCategories%5B0%5D=1247&subCategories%5B1%5D=1253&subCategories%5B2%5D=1255"
    "&subCategories%5B3%5D=1257&subCategories%5B4%5D=1262&subCategories%5B5%5D=1270"
    "&subCategories%5B6%5D=1274&subCategories%5B7%5D=1278&subCategories%5B8%5D=1279"
    "&subCategories%5B9%5D=1282&subCategories%5B10%5D=1289&subCategories%5B11%5D=1293"
    "&subCategories%5B12%5D=1294&subCategories%5B13%5D=1295&subCategories%5B14%5D=1296"
    "&subCategories%5B15%5D=1297&subCategories%5B16%5D=1298"
    "&maxPrice=400000"
)

_PROPERTY_HREF_RE = re.compile(r"/property/[a-z0-9]+", re.I)
_DEFAULT_MAX_PAGES = 5


def _page_url(page_num: int) -> str:
    return f"{BASE_URL}/properties/page/{page_num}?{SEARCH_QUERY}"


def _normalize_listing_url(href: str) -> str | None:
    href = href.strip()
    if not _PROPERTY_HREF_RE.search(href):
        return None
    full_url = href if href.startswith("http") else BASE_URL + href
    return full_url.split("#", 1)[0].split("?", 1)[0].rstrip("/")


def get_item_links_from_page(client: HttpClient, page_num: int) -> list[str]:
    """Pobiera unikalne linki do ogłoszeń 316 Real Estate z podanej strony."""
    url = _page_url(page_num)
    print(f"🔎 [316 Real Estate] Skanowanie strony {page_num}: {url}")

    try:
        response = client.get(url)
    except Exception as e:
        print(f"   └─ Błąd podczas pobierania strony {page_num}: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    links: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        normalized = _normalize_listing_url(a_tag["href"])
        if normalized:
            links.add(normalized)

    print(f"   └─ Znaleziono {len(links)} unikalnych ogłoszeń na stronie {page_num}.")
    return sorted(links)


def _meta_content(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", property=prop)
    if tag and tag.get("content"):
        return str(tag["content"]).strip()
    return None


def _normalize_detail_label(label: str) -> str:
    """Map portal labels to evaluator-friendly area keys."""
    compact = re.sub(r"\s+", " ", label).strip().lower()
    compact = compact.replace("(m 2)", "(m2)").replace("(m²)", "(m2)")
    if compact.startswith("total area"):
        return "Total area"
    if compact.startswith("plot area"):
        return "Plot area"
    if compact.startswith("internal area"):
        return "Internal area"
    if compact.startswith("external area"):
        return "External area"
    return label


def _detail_rows(soup: BeautifulSoup) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for block in soup.select(".property-detail-info-list"):
        for item in block.select("li"):
            label_el = item.select_one("label")
            if not label_el:
                continue
            label = label_el.get_text(" ", strip=True).rstrip(":")
            value_el = item.select_one("h6") or item.select_one("span")
            value = value_el.get_text(" ", strip=True) if value_el else item.get_text(" ", strip=True)
            value = re.sub(rf"^{re.escape(label)}\s*:?\s*", "", value, flags=re.I).strip()
            if label and value:
                rows.append((_normalize_detail_label(label), value))
    return rows


def _feature_lines(soup: BeautifulSoup) -> list[str]:
    features: list[str] = []
    for block in soup.select(".property-detail-info-list"):
        for item in block.select("li"):
            if item.select_one("label"):
                continue
            text = item.get_text(" ", strip=True)
            if text:
                features.append(text)
    return features


def _description_text(soup: BeautifulSoup) -> str | None:
    for heading in soup.select("h4.title-2"):
        if "description" not in heading.get_text(" ", strip=True).lower():
            continue
        container = heading.find_parent("div")
        if not container:
            continue
        paragraphs: list[str] = []
        for el in container.find_all(["p", "div"], recursive=True):
            text = el.get_text("\n", strip=True)
            if not text or text.lower() == "description":
                continue
            if any(text in existing for existing in paragraphs):
                continue
            paragraphs.append(text)
        if paragraphs:
            return "\n\n".join(paragraphs)
    return None


def _title_from_detail(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    og_title = _meta_content(soup, "og:title")
    if og_title:
        return og_title
    headline = soup.select_one("h1")
    locality = soup.select_one("h2")
    if headline and locality:
        return f"{headline.get_text(strip=True)} in {locality.get_text(strip=True)}"
    title, _ = extract_title_and_text(html)
    return title


def _raw_text_from_detail(html: str, url: str, title: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    price_el = soup.select_one(".property-price")
    price = price_el.get_text(" ", strip=True) if price_el else None
    property_type = soup.select_one("h1")
    locality_ref = soup.select_one("h2")
    og_description = _meta_content(soup, "og:description")
    description = _description_text(soup)
    detail_rows = _detail_rows(soup)
    features = _feature_lines(soup)

    lines = [
        f"Title: {title}",
        f"URL: {url}",
        f"Property type: {property_type.get_text(strip=True) if property_type else 'n/a'}",
        f"Locality: {locality_ref.get_text(strip=True) if locality_ref else 'n/a'}",
        f"Price: {price or 'n/a'}",
    ]
    for label, value in detail_rows:
        # Evaluator expects "Total area: 94.64 sqm" (unit after number).
        if label in {"Total area", "Internal area", "External area", "Plot area"}:
            if re.fullmatch(r"\d+(?:[.,]\d+)?", value):
                lines.append(f"{label}: {value} sqm")
                continue
        lines.append(f"{label}: {value}")
    if og_description:
        lines.extend(["", "Summary:", og_description])
    if features:
        lines.extend(["", "Property Features:"])
        lines.extend(features)
    if description:
        lines.extend(["", "Description:", description])
    return "\n".join(lines).strip()


def parse_re316_html(html: str, url: str) -> ScrapedListing:
    """Extract a staging listing from a 316 Real Estate property page."""
    title = _title_from_detail(html)
    if title == "Brak tytułu":
        title = "316 Real Estate Property"

    raw_text = _raw_text_from_detail(html, url, title)
    if not raw_text.strip():
        _, raw_text = extract_title_and_text(html)

    return ScrapedListing(
        url=url,
        title=title,
        raw_text=raw_text,
        source=SOURCE,
        scraped_at=utc_now_iso(),
    )


def scrape_item_details(client: HttpClient, url: str) -> ScrapedListing | None:
    """Pobiera treść pojedynczego ogłoszenia z 316 Real Estate."""
    try:
        response = client.get(url)
    except Exception as e:
        print(f"   └─ Błąd pobierania {url}: {e}")
        return None

    return parse_re316_html(response.text, url)


def run_re316_scraper(max_pages: int = _DEFAULT_MAX_PAGES) -> list[dict]:
    """Główna funkcja uruchamiająca pobieranie z 316 Real Estate."""
    client = HttpClient()
    all_item_urls: set[str] = set()

    print(f"🚀 Rozpoczynam pobieranie z 316 Real Estate (strony 1-{max_pages})...\n")

    for page in range(1, max_pages + 1):
        links = get_item_links_from_page(client, page)
        if not links:
            print("   └─ Pusta strona — koniec paginacji.")
            break
        all_item_urls.update(links)
        if page < max_pages:
            time.sleep(random.uniform(1.0, 2.0))

    print(f"\n📊 Łącznie zebrano {len(all_item_urls)} unikalnych ofert z 316 Real Estate.\n")

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
    run_re316_scraper(max_pages=_DEFAULT_MAX_PAGES)
