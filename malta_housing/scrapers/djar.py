"""Djar.ai scraper — merges results into scraped_listings.json."""

from __future__ import annotations

import random
import time

from bs4 import BeautifulSoup

from malta_housing.common import HttpClient, extract_title_and_text, merge_staging
from malta_housing.geo import is_gozo_listing
from malta_housing.models import ScrapedListing, utc_now_iso

BASE_URL = "https://www.djar.ai"

SEARCH_URL = (
    "https://www.djar.ai/search"
    "?type=sale&propertyTypes=Apartment,Penthouse,Maisonette"
    "&priceMin=200000&priceMax=400000"
)
SOURCE = "djar"


def get_item_links_from_page(client: HttpClient, page_num: int) -> list[str]:
    """Pobiera unikalne linki do ogłoszeń z djar.ai (?page=X)."""
    url = f"{SEARCH_URL}&page={page_num}"
    print(f"🔎 [Djar.ai] Skanowanie strony {page_num}: {url}")

    try:
        response = client.get(url)
    except Exception as e:
        print(f"   └─ Błąd podczas pobierania strony {page_num}: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    links: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if "/property/" not in href:
            continue
        full_url = href if href.startswith("http") else BASE_URL + href
        # Drop query/hash fragments
        full_url = full_url.split("#", 1)[0].split("?", 1)[0]
        links.add(full_url)

    print(f"   └─ Znaleziono {len(links)} unikalnych ogłoszeń na stronie {page_num}.")
    return list(links)


def scrape_item_details(client: HttpClient, url: str) -> ScrapedListing | None:
    """Pobiera treść pojedynczego ogłoszenia z djar.ai."""
    try:
        response = client.get(url)
    except Exception as e:
        print(f"   └─ Błąd pobierania {url}: {e}")
        return None

    title, raw_text = extract_title_and_text(response.text)
    if title == "Brak tytułu":
        title = "Djar.ai Property"

    return ScrapedListing(
        url=url,
        title=title,
        raw_text=raw_text,
        source=SOURCE,
        scraped_at=utc_now_iso(),
    )


def run_djar_scraper(max_pages: int = 3) -> list[dict]:
    """Główna funkcja uruchamiająca pobieranie z djar.ai."""
    client = HttpClient()
    all_item_urls: set[str] = set()

    print(f"🚀 Rozpoczynam pobieranie z djar.ai (strony 1-{max_pages})...\n")

    for page in range(1, max_pages + 1):
        links = get_item_links_from_page(client, page)
        all_item_urls.update(links)
        time.sleep(random.uniform(1.2, 2.5))

    print(f"\n📊 Łącznie zebrano {len(all_item_urls)} unikalnych ofert z djar.ai.\n")

    scraped_data: list[ScrapedListing] = []
    skipped_gozo = 0
    for i, url in enumerate(all_item_urls, 1):
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
        f" (pominięto {skipped_gozo} Gozo); "
        f"staging ma teraz {len(merged)} unikalnych URL-i."
    )
    print("👉 Możesz teraz uruchomić: python -m malta_housing parse")
    return [item.model_dump() for item in scraped_data]


if __name__ == "__main__":
    run_djar_scraper(max_pages=3)
