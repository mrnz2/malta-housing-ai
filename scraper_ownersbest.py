import json
import random
import time
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://ownersbest.com.mt"

SEARCH_URL = (
    "https://ownersbest.com.mt/malta-properties/"
    "?prop_tag=sale&type%5B0%5D=3&type%5B1%5D=10&type%5B2%5D=5&type%5B3%5D=15&type%5B4%5D=1&maxprice=400000"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def get_item_links_from_page(page_num: int) -> list[str]:
    """Pobiera unikalne linki do ogłoszeń Owners Best z podanej strony paginacji (?pg=X)."""
    url = f"{SEARCH_URL}&pg={page_num}"
    print(f"🔎 [Owners Best] Skanowanie strony {page_num}: {url}")

    try:
        response = requests.get(url, headers=HEADERS, timeout=12)
        response.raise_for_status()
    except Exception as e:
        print(f"   └─ Błąd podczas pobierania strony {page_num}: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    links = set()

    # Wyłapujemy linki zawierające charakterystyczne dla Owners Best frazy
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if "/malta-property/" in href and "real-estate-detail-" in href:
            full_url = href if href.startswith("http") else BASE_URL + href
            links.add(full_url)

    print(f"   └─ Znaleziono {len(links)} unikalnych ogłoszeń na stronie {page_num}.")
    return list(links)


def scrape_item_details(url: str) -> dict | None:
    """Pobiera treść pojedynczego ogłoszenia z Owners Best."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=12)
        response.raise_for_status()
    except Exception as e:
        print(f"   └─ Błąd pobierania {url}: {e}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    # Wyciąganie nagłówka (tytułu)
    title_tag = soup.find("h1") or soup.find("h2") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "Owners Best Property"

    # Czyszczenie zbędnych elementów HTML przed pobraniem czystego tekstu
    for element in soup(["script", "style", "nav", "footer", "header"]):
        element.decompose()

    raw_text = soup.get_text(separator="\n", strip=True)

    return {"url": url, "title": title, "raw_text": raw_text}


def run_ownersbest_scraper(max_pages: int = 3):
    """Główna funkcja uruchamiająca pobieranie z Owners Best."""
    all_item_urls = set()

    print(f"🚀 Rozpoczynam pobieranie z Owners Best (strony 1-{max_pages})...\n")

    # 1. Zbieranie linków
    for page in range(1, max_pages + 1):
        links = get_item_links_from_page(page)
        all_item_urls.update(links)
        time.sleep(random.uniform(1.2, 2.5))

    print(f"\n📊 Łącznie zebrano {len(all_item_urls)} unikalnych ofert z Owners Best.\n")

    # 2. Pobieranie treści każdej oferty
    scraped_data = []
    for i, url in enumerate(all_item_urls, 1):
        print(f"[{i}/{len(all_item_urls)}] Pobieranie opisu: {url}")
        item_data = scrape_item_details(url)
        if item_data:
            scraped_data.append(item_data)

        time.sleep(random.uniform(1.0, 2.0))

    # 3. Zapis do stagingu dla parsera
    with open("scraped_listings.json", "w", encoding="utf-8") as f:
        json.dump(scraped_data, f, indent=2, ensure_ascii=False)

    print(
        f"\n✅ Zakończono! Zapisano {len(scraped_data)} ogłoszeń do 'scraped_listings.json'."
    )
    print("👉 Możesz teraz uruchomić: python parser.py")


if __name__ == "__main__":
    run_ownersbest_scraper(max_pages=3)