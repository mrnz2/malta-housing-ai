"""Owners Best scraper — merges results into scraped_listings.json."""

from __future__ import annotations

import random
import time

from bs4 import BeautifulSoup

from malta_housing.common import HttpClient, extract_title_and_text, load_hidden_urls, merge_staging
from malta_housing.geo import is_gozo_listing
from malta_housing.models import ScrapedListing, utc_now_iso

BASE_URL = "https://ownersbest.com.mt"

SEARCH_URL = (
    "https://ownersbest.com.mt/malta-properties/"
    "?prop_tag=sale&type%5B0%5D=3&type%5B1%5D=10&type%5B2%5D=5&type%5B3%5D=15&type%5B4%5D=1&maxprice=400000"
)
SOURCE = "ownersbest"


def get_item_links_from_page(client: HttpClient, page_num: int) -> list[str]:
    """Pobiera unikalne linki do ogłoszeń Owners Best z podanej strony paginacji (?pg=X)."""
    url = f"{SEARCH_URL}&pg={page_num}"
    print(f"🔎 [Owners Best] Skanowanie strony {page_num}: {url}")

    try:
        response = client.get(url)
    except Exception as e:
        print(f"   └─ Błąd podczas pobierania strony {page_num}: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    links: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if "/malta-property/" in href and "real-estate-detail-" in href:
            full_url = href if href.startswith("http") else BASE_URL + href
            links.add(full_url)

    print(f"   └─ Znaleziono {len(links)} unikalnych ogłoszeń na stronie {page_num}.")
    return list(links)


def scrape_item_details(client: HttpClient, url: str) -> ScrapedListing | None:
    """Pobiera treść pojedynczego ogłoszenia z Owners Best."""
    try:
        response = client.get(url)
    except Exception as e:
        print(f"   └─ Błąd pobierania {url}: {e}")
        return None

    title, raw_text = extract_title_and_text(response.text)
    if title == "Brak tytułu":
        title = "Owners Best Property"

    return ScrapedListing(
        url=url,
        title=title,
        raw_text=raw_text,
        source=SOURCE,
        scraped_at=utc_now_iso(),
    )


def run_ownersbest_scraper(max_pages: int = 3) -> list[dict]:
    """Główna funkcja uruchamiająca pobieranie z Owners Best."""
    client = HttpClient()
    all_item_urls: set[str] = set()

    print(f"🚀 Rozpoczynam pobieranie z Owners Best (strony 1-{max_pages})...\n")

    for page in range(1, max_pages + 1):
        links = get_item_links_from_page(client, page)
        all_item_urls.update(links)
        time.sleep(random.uniform(1.2, 2.5))

    print(f"\n📊 Łącznie zebrano {len(all_item_urls)} unikalnych ofert z Owners Best.\n")

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
    run_ownersbest_scraper(max_pages=3)
