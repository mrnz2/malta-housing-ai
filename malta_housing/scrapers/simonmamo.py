"""Simon Mamo scraper — merges results into scraped_listings.json."""

from __future__ import annotations

import random
import re
import time

from bs4 import BeautifulSoup

from malta_housing.common import (
    HttpClient,
    extract_title_and_text,
    load_hidden_urls,
    merge_staging,
)
from malta_housing.geo import is_gozo_listing
from malta_housing.models import ScrapedListing, utc_now_iso

BASE_URL = "https://www.simonmamo.com"
SEARCH_QUERY = (
    "status=buy&child_status=rent&keyword"
    "&category%5B0%5D=2&category%5B1%5D=3&category%5B2%5D=4&category%5B3%5D=9"
    "&category%5B4%5D=15&category%5B5%5D=16&category%5B6%5D=17"
    "&prices%5B0%5D=200000-250000&prices%5B1%5D=250000-300000"
    "&prices%5B2%5D=300000-400000&available_from&available_to"
)
SOURCE = "simonmamo"

# curl_cffi chrome124 sets browser headers; DEFAULT_HEADERS mismatch triggers CF 403.
HEADERS = {
    "Referer": f"{BASE_URL}/",
}

_PROPERTY_HREF_RE = re.compile(r"/property/sm-\d+/", re.I)


def _page_url(page_num: int) -> str:
    return f"{BASE_URL}/search-results/page/{page_num}/?{SEARCH_QUERY}"


def _normalize_listing_url(href: str) -> str | None:
    href = href.strip()
    if not _PROPERTY_HREF_RE.search(href):
        return None
    full_url = href if href.startswith("http") else BASE_URL + href
    full_url = full_url.split("#", 1)[0].split("?", 1)[0]
    if not full_url.endswith("/"):
        full_url += "/"
    return full_url


def _is_blocked_response(response, *, expect_listings: bool = False) -> bool:
    if response.status_code in {403, 429}:
        return True
    text = response.text or ""
    if "403 - Forbidden" in text or "cf-browser-verification" in text.lower():
        return True
    return expect_listings and "/property/sm-" not in text


def get_item_links_from_page(client: HttpClient, page_num: int) -> list[str]:
    """Pobiera unikalne linki do ogłoszeń Simon Mamo (page/N/)."""
    url = _page_url(page_num)
    print(f"🔎 [Simon Mamo] Skanowanie strony {page_num}: {url}")

    try:
        response = client.get(url)
    except Exception as e:
        print(f"   └─ Błąd podczas pobierania strony {page_num}: {e}")
        return []

    if _is_blocked_response(response, expect_listings=True):
        print(
            f"   └─ Blokada Cloudflare (HTTP {response.status_code}). "
            "Spróbuj ponownie za chwilę lub zmniejsz częstotliwość zapytań."
        )
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    links: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        normalized = _normalize_listing_url(a_tag["href"])
        if normalized:
            links.add(normalized)

    print(f"   └─ Znaleziono {len(links)} unikalnych ogłoszeń na stronie {page_num}.")
    return list(links)


def scrape_item_details(client: HttpClient, url: str) -> ScrapedListing | None:
    """Pobiera treść pojedynczego ogłoszenia z Simon Mamo."""
    try:
        response = client.get(url)
    except Exception as e:
        print(f"   └─ Błąd pobierania {url}: {e}")
        return None

    if _is_blocked_response(response):
        print(f"   └─ Blokada Cloudflare przy {url}")
        return None

    title, raw_text = extract_title_and_text(response.text)
    if title == "Brak tytułu":
        title = "Simon Mamo Property"

    return ScrapedListing(
        url=url,
        title=title,
        raw_text=raw_text,
        source=SOURCE,
        scraped_at=utc_now_iso(),
    )


def run_simonmamo_scraper(max_pages: int = 3) -> list[dict]:
    """Główna funkcja uruchamiająca pobieranie z Simon Mamo."""
    client = HttpClient(headers=HEADERS, impersonate="chrome124", timeout=25.0)
    all_item_urls: set[str] = set()

    print(f"🚀 Rozpoczynam pobieranie z Simon Mamo (strony 1-{max_pages})...\n")

    for page in range(1, max_pages + 1):
        links = get_item_links_from_page(client, page)
        if not links and page > 1:
            print("   └─ Pusta strona — koniec paginacji.")
            break
        all_item_urls.update(links)
        time.sleep(random.uniform(1.2, 2.5))

    print(f"\n📊 Łącznie zebrano {len(all_item_urls)} unikalnych ofert z Simon Mamo.\n")

    scraped_data: list[ScrapedListing] = []
    skipped_gozo = 0
    skipped_hidden = 0
    hidden_urls = load_hidden_urls()
    for i, url in enumerate(all_item_urls, 1):
        if url in hidden_urls:
            skipped_hidden += 1
            print(f"[{i}/{len(all_item_urls)}] Pominięto (ukryte): {url}")
            continue
        print(f"[{i}/{len(all_item_urls)}] Pobieranie opisu: {url}")
        item_data = scrape_item_details(client, url)
        if item_data:
            if is_gozo_listing(title=item_data.title, url=item_data.url):
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
    run_simonmamo_scraper(max_pages=3)
