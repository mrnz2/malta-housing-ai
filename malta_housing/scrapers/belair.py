"""Belair Malta scraper — AJAX filter API merged into scraped_listings.json."""

from __future__ import annotations

import json
import random
import re
import time
from urllib.parse import unquote_plus

from bs4 import BeautifulSoup

from malta_housing.common import HttpClient, extract_title_and_text, merge_staging
from malta_housing.geo import is_gozo_listing
from malta_housing.models import ScrapedListing, utc_now_iso

BASE_URL = "https://belair.com.mt"
AJAX_URL = f"{BASE_URL}/wp-admin/admin-ajax.php"
SOURCE = "belair"

FILTER_PAYLOAD_BASE: dict[str, str] = {
    "action": "filter_properties",
    "city": "",
    "status": "for_sale",
    "minPrice": "200000",
    "maxPrice": "400000",
    "bedrooms": "Any",
    "propertyType": "500003,2,3,6,7,8,296,11,13",
    "selectFilter": "listing_type",
    "default": "",
    "keep_residential_page_settings": "yes",
}

AJAX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE_URL}/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
}

_PROPERTY_HREF_RE = re.compile(r"/property/\d+", re.I)
_SHOWING_RE = re.compile(r"Showing\s+(\d+)-(\d+)\s+out\s+of\s+(\d+)", re.I)


def _filter_payload(page_num: int) -> dict[str, str]:
    return {**FILTER_PAYLOAD_BASE, "page_to_go": str(page_num)}


def _normalize_listing_url(href: str) -> str | None:
    href = href.strip()
    if not _PROPERTY_HREF_RE.search(href):
        return None
    full_url = href if href.startswith("http") else BASE_URL + href
    return full_url.split("#", 1)[0].split("?", 1)[0]


def _links_from_filter_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: set[str] = set()
    for a_tag in soup.find_all("a", href=True):
        normalized = _normalize_listing_url(a_tag["href"])
        if normalized:
            links.add(normalized)
    return sorted(links)


def fetch_listing_links(client: HttpClient, page_num: int) -> list[str]:
    """POST one filter page and return unique property URLs from the HTML fragment."""
    print(f"🔎 [Belair] AJAX strona {page_num}")
    try:
        response = client.session.post(
            AJAX_URL,
            headers=AJAX_HEADERS,
            data=_filter_payload(page_num),
            timeout=client.timeout,
        )
        response.raise_for_status()
    except Exception as e:
        print(f"   └─ Błąd podczas pobierania strony {page_num}: {e}")
        return []

    try:
        payload = response.json()
    except json.JSONDecodeError as e:
        print(f"   └─ Niepoprawny JSON na stronie {page_num}: {e}")
        return []

    if not isinstance(payload, dict) or not payload.get("success"):
        print(f"   └─ API zwróciło success=false na stronie {page_num}.")
        return []

    html = payload.get("data") or ""
    if not isinstance(html, str):
        print(f"   └─ Brak HTML w odpowiedzi na stronie {page_num}.")
        return []

    links = _links_from_filter_html(html)
    match = _SHOWING_RE.search(html)
    if match:
        start, end, total = match.groups()
        print(f"   └─ Znaleziono {len(links)} ogłoszeń (Showing {start}-{end} of {total}).")
    else:
        print(f"   └─ Znaleziono {len(links)} ogłoszeń.")
    return links


def _title_from_detail(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        return str(og_title["content"]).strip()
    heading = soup.find("h4", class_="page_title") or soup.find("h4")
    if heading:
        return heading.get_text(" ", strip=True)
    title, _ = extract_title_and_text(html)
    return title


def _meta_content(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", property=prop)
    if tag and tag.get("content"):
        return str(tag["content"]).strip()
    return None


def _locality_and_condition(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    heading = soup.select_one(".singel_page_card h5, .single_content h5")
    if not heading:
        return None, None
    locality_parts: list[str] = []
    condition: str | None = None
    for child in heading.children:
        if getattr(child, "name", None) == "span":
            span_text = child.get_text(" ", strip=True)
            if span_text.startswith("|"):
                condition = span_text.lstrip("| ").strip() or None
        elif isinstance(child, str):
            text = child.strip()
            if text:
                locality_parts.append(text)
    locality = " ".join(locality_parts).strip() or None
    if locality is None:
        full = heading.get_text(" ", strip=True)
        if " | " in full:
            locality, condition = [part.strip() for part in full.split(" | ", 1)]
        else:
            locality = full or None
    return locality, condition


def _room_info_lines(soup: BeautifulSoup) -> list[str]:
    lines: list[str] = []
    for item in soup.select(".singel_page_card .room-info li, .single_content .room-info li"):
        text = item.get_text(" ", strip=True)
        if not text:
            continue
        lowered = text.lower()
        if "bedroom" in lowered:
            lines.append(f"Bedrooms: {text.split()[0]}")
        elif "bathroom" in lowered:
            lines.append(f"Bathrooms: {text.split()[0]}")
        elif "m" in lowered:
            area_match = re.search(r"([\d.]+)\s*m", text, re.I)
            if area_match:
                lines.append(f"Area m2: {area_match.group(1)}")
            else:
                lines.append(f"Area: {text}")
    return lines


def _reference_number(soup: BeautifulSoup, url: str) -> str | None:
    strong = soup.select_one(".code_number strong")
    if strong:
        return strong.get_text(strip=True) or None
    match = re.search(r"/property/(\d+)", url)
    if match:
        return match.group(1)
    return None


def _about_description(soup: BeautifulSoup) -> str | None:
    for heading in soup.select("h3.heading-24, h3.vc_custom_heading"):
        if "about the property" not in heading.get_text(" ", strip=True).lower():
            continue
        card = heading.find_parent("div", class_="block_card")
        if not card:
            continue
        content = card.select_one(".block_content")
        if content:
            return content.get_text("\n", strip=True) or None
    return None


def _feature_lines(soup: BeautifulSoup) -> list[str]:
    features: list[str] = []
    for item in soup.select(".featured_icon li"):
        text = item.get_text(" ", strip=True)
        if text:
            features.append(text)
    return features


def _agent_name(soup: BeautifulSoup) -> str | None:
    agent = soup.select_one(".enquiry .white_card_content h6")
    if agent:
        return agent.get_text(strip=True) or None
    return None


def _map_locality(soup: BeautifulSoup) -> str | None:
    iframe = soup.select_one(".map_location iframe[src]")
    if not iframe:
        return None
    src = str(iframe.get("src", ""))
    match = re.search(r"[?&]q=([^&\"']+)", src)
    if not match:
        return None
    return unquote_plus(match.group(1)).strip() or None


def _raw_text_from_detail(html: str, url: str, title: str) -> str:
    """Build LLM-friendly text from Belair property markup (not full page dump)."""
    soup = BeautifulSoup(html, "html.parser")
    locality, condition = _locality_and_condition(soup)
    price_el = soup.select_one(".single_price")
    price = price_el.get_text(" ", strip=True) if price_el else None
    page_title = soup.select_one("h4.page_title")
    headline = page_title.get_text(" ", strip=True) if page_title else title
    reference = _reference_number(soup, url)
    og_description = _meta_content(soup, "og:description")
    description = _about_description(soup)
    epc_el = soup.select_one(".epc-row")
    epc = epc_el.get_text(" ", strip=True) if epc_el else None
    agent = _agent_name(soup)
    map_query = _map_locality(soup)

    lines = [
        f"Title: {title}",
        f"URL: {url}",
        f"Headline: {headline}",
        f"Reference No.: {reference or 'n/a'}",
        f"Price: {price or 'n/a'}",
        f"Locality: {locality or 'n/a'}",
        f"Condition: {condition or 'n/a'}",
        f"Map location: {map_query or 'n/a'}",
        f"Agent: {agent or 'n/a'}",
    ]
    lines.extend(_room_info_lines(soup))
    if og_description:
        lines.extend(["", "Summary:", og_description])
    if epc:
        lines.extend(["", epc])
    features = _feature_lines(soup)
    if features:
        lines.extend(["", "Property Features:"])
        lines.extend(features)
    if description:
        lines.extend(["", "Description:", description])
    return "\n".join(lines).strip()


def parse_belair_html(html: str, url: str) -> ScrapedListing:
    """Extract a staging listing from Belair property page HTML."""
    title = _title_from_detail(html)
    if title == "Brak tytułu":
        title = "Belair Property"

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
    """Fetch a single Belair property page."""
    try:
        response = client.get(url)
    except Exception as e:
        print(f"   └─ Błąd pobierania {url}: {e}")
        return None

    return parse_belair_html(response.text, url)


def run_belair_scraper(max_pages: int = 3) -> list[dict]:
    """Fetch up to max_pages from Belair Malta, merge into staging."""
    client = HttpClient(headers=AJAX_HEADERS, timeout=20.0)
    all_item_urls: set[str] = set()

    print(f"🚀 Rozpoczynam pobieranie z Belair Malta (strony 1-{max_pages})...\n")

    for page in range(1, max_pages + 1):
        links = fetch_listing_links(client, page)
        if not links:
            print("   └─ Pusta strona — koniec paginacji.")
            break
        all_item_urls.update(links)
        if page < max_pages:
            time.sleep(random.uniform(0.8, 1.6))

    print(f"\n📊 Łącznie zebrano {len(all_item_urls)} unikalnych ofert z Belair Malta.\n")

    scraped_data: list[ScrapedListing] = []
    skipped_gozo = 0
    for i, url in enumerate(sorted(all_item_urls), 1):
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
        f" (pominięto {skipped_gozo} Gozo); "
        f"staging ma teraz {len(merged)} unikalnych URL-i."
    )
    print("👉 Możesz teraz uruchomić: python -m malta_housing parse")
    return [item.model_dump() for item in scraped_data]


if __name__ == "__main__":
    run_belair_scraper(max_pages=3)
