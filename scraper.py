import json
import random
import time
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.maltapark.com"
CATEGORY_URL = "https://www.maltapark.com/listings/category/248"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def get_item_links_from_page(page_num: int) -> list[str]:
    """Pobiera listę unikalnych linków do ogłoszeń z konkretnej podstrony listy."""
    # MaltaPark do paginacji kategorii używa parametru ?p= (np. ?p=2)
    # W razie potrzeby dodajemy też zapasową obsługę ?page=
    if page_num == 1:
        url = CATEGORY_URL
    else:
        url = f"{CATEGORY_URL}?p={page_num}"

    print(f"🔎 Skanowanie strony {page_num}: {url}")

    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"   └─ Błąd podczas pobierania strony {page_num}: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    links = set()

    # Wyciągamy wszystkie odnośniki prowadzące do kart szczegółów ogłoszeń
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if "/item/details/" in href:
            full_url = href if href.startswith("http") else BASE_URL + href
            links.add(full_url)

    print(f"   └─ Znaleziono {len(links)} unikalnych ogłoszeń na stronie {page_num}.")
    return list(links)


def scrape_item_details(url: str) -> dict | None:
    """Pobiera stronę pojedynczego ogłoszenia i wyciąga tytuł oraz surowy tekst."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"   └─ Błąd pobierania {url}: {e}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    # Wyciągamy nagłówek ogłoszenia
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "Brak tytułu"

    # Wyciągamy cały czytelny tekst ze strony
    raw_text = soup.get_text(separator="\n", strip=True)

    return {"url": url, "title": title, "raw_text": raw_text}


def run_scraper(max_pages: int = 3):
    """Główna pętla scrapera: zbiera linki z podstron i pobiera treść ogłoszeń."""
    all_item_urls = set()

    print(f"🚀 Rozpoczynam pobieranie ogłoszeń z {max_pages} stron...\n")

    # 1. Zbieranie odnośników ze wszystkich stron
    for page in range(1, max_pages + 1):
        page_links = get_item_links_from_page(page)
        all_item_urls.update(page_links)
        # Losowa pauza dla ochrony przed blokadą
        time.sleep(random.uniform(1.0, 2.0))

    print(f"\n📊 Łącznie zebrano {len(all_item_urls)} unikalnych linków do pobrania.\n")

    # 2. Pobieranie zawartości każdego ogłoszenia
    scraped_data = []
    for i, url in enumerate(all_item_urls, 1):
        print(f"[{i}/{len(all_item_urls)}] Pobieranie: {url}")
        item_data = scrape_item_details(url)
        if item_data:
            scraped_data.append(item_data)

        # Bezpieczne opóźnienie między zapytaniami
        time.sleep(random.uniform(1.0, 2.5))

    # 3. Zapis do pliku strefy przejściowej (staging)
    with open("scraped_listings.json", "w", encoding="utf-8") as f:
        json.dump(scraped_data, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Zakończono! Pobrano {len(scraped_data)} ogłoszeń i zapisano w 'scraped_listings.json'.")


if __name__ == "__main__":
    # Możesz zmienić wartość max_pages na np. 5 lub 10, aby zebrać większą próbkę
    run_scraper(max_pages=3)