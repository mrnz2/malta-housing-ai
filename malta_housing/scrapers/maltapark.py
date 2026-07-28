"""MaltaPark scraper — merges results into scraped_listings.json."""

from __future__ import annotations

import random
import time

from bs4 import BeautifulSoup

from malta_housing.common import HttpClient, extract_title_and_text, merge_staging
from malta_housing.geo import is_gozo_listing
from malta_housing.models import ScrapedListing, utc_now_iso

BASE_URL = "https://www.maltapark.com"
# Property For Sale (c=248), €100k–€400k — matches Maltapark search filters.
SEARCH_URL = (
    "https://www.maltapark.com/search/"
    "?psrch=1&sortby=&c=248&bedrooms=-1"
    "&minp=100000&maxp=400000"
    "&region=-1&search=&haspool=false&excludeagents=false"
)
SOURCE = "maltapark"


def get_item_links_from_page(client: HttpClient, page_num: int) -> list[str]:
    """Pobiera listę unikalnych linków do ogłoszeń z konkretnej podstrony listy."""
    url = f"{SEARCH_URL}&page={page_num}"

    print(f"🔎 Skanowanie strony {page_num}: {url}")

    try:
        response = client.get(url)
    except Exception as e:
        print(f"   └─ Błąd podczas pobierania strony {page_num}: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    links: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if "/item/details/" in href:
            full_url = href if href.startswith("http") else BASE_URL + href
            links.add(full_url)

    print(f"   └─ Znaleziono {len(links)} unikalnych ogłoszeń na stronie {page_num}.")
    return list(links)


def scrape_item_details(client: HttpClient, url: str) -> ScrapedListing | None:
    """Pobiera stronę pojedynczego ogłoszenia i wyciąga tytuł oraz surowy tekst."""
    try:
        response = client.get(url)
    except Exception as e:
        print(f"   └─ Błąd pobierania {url}: {e}")
        return None

    title, raw_text = extract_title_and_text(response.text)
    return ScrapedListing(
        url=url,
        title=title,
        raw_text=raw_text,
        source=SOURCE,
        scraped_at=utc_now_iso(),
    )


def run_scraper(max_pages: int = 3) -> list[dict]:
    """Główna pętla scrapera: zbiera linki z podstron i pobiera treść ogłoszeń."""
    client = HttpClient()
    all_item_urls: set[str] = set()

    print(f"🚀 Rozpoczynam pobieranie ogłoszeń z {max_pages} stron (MaltaPark)...\n")

    for page in range(1, max_pages + 1):
        page_links = get_item_links_from_page(client, page)
        all_item_urls.update(page_links)
        time.sleep(random.uniform(1.0, 2.0))

    print(f"\n📊 Łącznie zebrano {len(all_item_urls)} unikalnych linków do pobrania.\n")

    scraped_data: list[ScrapedListing] = []
    skipped_gozo = 0
    for i, url in enumerate(all_item_urls, 1):
        print(f"[{i}/{len(all_item_urls)}] Pobieranie: {url}")
        item_data = scrape_item_details(client, url)
        if item_data:
            if is_gozo_listing(title=item_data.title, url=item_data.url):
                skipped_gozo += 1
                print("   └─ Pominięto (Gozo).")
            else:
                scraped_data.append(item_data)
        time.sleep(random.uniform(1.0, 2.5))

    merged = merge_staging(scraped_data)
    print(
        f"\n✅ Zakończono! Pobrano {len(scraped_data)} ogłoszeń"
        f" (pominięto {skipped_gozo} Gozo); "
        f"staging ma teraz {len(merged)} unikalnych URL-i."
    )
    return [item.model_dump() for item in scraped_data]


if __name__ == "__main__":
    run_scraper(max_pages=3)
