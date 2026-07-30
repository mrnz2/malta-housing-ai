"""Property Market Malta scraper — merges results into scraped_listings.json."""

from __future__ import annotations

import random
import time

from bs4 import BeautifulSoup

from malta_housing.common import (
    DEFAULT_HEADERS,
    HttpClient,
    extract_title_and_text,
    merge_staging,
    strip_noise_tags,
)
from malta_housing.geo import is_gozo_listing
from malta_housing.models import ScrapedListing, utc_now_iso

BASE_URL = "https://www.propertymarket.com.mt"
SEARCH_URL = f"{BASE_URL}/for-sale/"
# Site accepts ``pp`` only when the full filter query is present (not bare ``?pp=N``).
SEARCH_QUERY = "pt=0&currentLocations=&mnp=15&mxp=19&pc=0&nb=0&btnForSale=Search"
SOURCE = "propertymarket"

HEADERS = {
    **DEFAULT_HEADERS,
    "Referer": f"{BASE_URL}/",
}


def _page_url(page_num: int) -> str:
    base = f"{SEARCH_URL}?{SEARCH_QUERY}"
    if page_num <= 1:
        return base
    return f"{base}&pp={page_num}"


def _normalize_listing_url(href: str) -> str | None:
    href = href.strip()
    if "/view/" not in href:
        return None
    full_url = href if href.startswith("http") else BASE_URL + href
    full_url = full_url.split("#", 1)[0].split("?", 1)[0]
    if "/view/" not in full_url:
        return None
    # Detail pages 403 without a trailing slash.
    if not full_url.endswith("/"):
        full_url += "/"
    return full_url


def _is_blocked_response(response) -> bool:
    """Detect SiteGround bot challenge / empty challenge HTML."""
    if response.status_code in {202, 403, 429}:
        return True
    headers = {k.lower(): v for k, v in response.headers.items()}
    if headers.get("sg-captcha"):
        return True
    text = response.text or ""
    return "sgcaptcha" in text or "Robot Challenge Screen" in text


def get_item_links_from_page(client: HttpClient, page_num: int) -> list[str]:
    """Pobiera unikalne linki do ogłoszeń z Property Market (?pp=X)."""
    url = _page_url(page_num)
    print(f"🔎 [Property Market] Skanowanie strony {page_num}: {url}")

    try:
        response = client.get(url)
    except Exception as e:
        print(f"   └─ Błąd podczas pobierania strony {page_num}: {e}")
        return []

    if _is_blocked_response(response):
        print(
            f"   └─ Blokada SiteGround (HTTP {response.status_code}). "
            "Spróbuj ponownie za chwilę lub zmniejsz częstotliwość zapytań."
        )
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    links: set[str] = set()

    for listing_div in soup.find_all("div", class_="searchResultListing"):
        for a_tag in listing_div.find_all("a", href=True):
            normalized = _normalize_listing_url(a_tag["href"])
            if normalized:
                links.add(normalized)

    print(f"   └─ Znaleziono {len(links)} unikalnych ogłoszeń na stronie {page_num}.")
    return list(links)


def _meta_lines(soup: BeautifulSoup) -> list[str]:
    lines: list[str] = []
    for tag in soup.find_all("meta"):
        key = tag.get("name") or tag.get("property") or tag.get("itemprop")
        content = (tag.get("content") or "").strip()
        if key and content:
            lines.append(f"{key}: {content}")
    return lines


def _main_content_text(main: BeautifulSoup) -> str:
    strip_noise_tags(main)
    for element in main.find_all(
        True,
        class_=lambda c: bool(c)
        and any(
            token in " ".join(c if isinstance(c, list) else [c]).lower()
            for token in ("advert", "adsbygoogle", "promo", "banner", "sponsor")
        ),
    ):
        element.decompose()
    return main.get_text(separator="\n", strip=True)


def scrape_item_details(client: HttpClient, url: str) -> ScrapedListing | None:
    """Pobiera treść pojedynczego ogłoszenia z Property Market."""
    try:
        response = client.get(url)
    except Exception as e:
        print(f"   └─ Błąd pobierania {url}: {e}")
        return None

    if _is_blocked_response(response):
        print(f"   └─ Blokada SiteGround przy {url}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    main = soup.find("main")
    meta_text = "\n".join(_meta_lines(soup))
    main_text = _main_content_text(main) if main else ""

    parts = [part for part in (meta_text, main_text) if part]
    raw_text = "\n\n".join(parts)

    title_tag = None
    if main:
        title_tag = main.select_one("#myListingDetailsTitle h1") or main.find("h1")
    if not title_tag:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = og_title["content"].strip()
        else:
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else "Brak tytułu"
    else:
        title = title_tag.get_text(strip=True)

    if not raw_text:
        title, raw_text = extract_title_and_text(response.text)

    if title == "Brak tytułu":
        title = "Property Market Listing"

    return ScrapedListing(
        url=url,
        title=title,
        raw_text=raw_text,
        source=SOURCE,
        scraped_at=utc_now_iso(),
    )


def run_propertymarket_scraper(max_pages: int = 3) -> list[dict]:
    """Główna funkcja uruchamiająca pobieranie z Property Market Malta."""
    # TLS impersonation + automatic SiteGround PoW solve (HTTP 202 → _I_ cookie).
    client = HttpClient(headers=HEADERS, impersonate="chrome124", timeout=25.0)
    all_item_urls: set[str] = set()

    print(f"🚀 Rozpoczynam pobieranie z Property Market (strony 1-{max_pages})...\n")

    for page in range(1, max_pages + 1):
        links = get_item_links_from_page(client, page)
        all_item_urls.update(links)
        time.sleep(random.uniform(1.0, 2.5))

    print(f"\n📊 Łącznie zebrano {len(all_item_urls)} unikalnych ofert z Property Market.\n")

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
        time.sleep(random.uniform(1.0, 2.5))

    merged = merge_staging(scraped_data)
    print(
        f"\n✅ Zakończono! Pobrano {len(scraped_data)} ogłoszeń"
        f" (pominięto {skipped_gozo} Gozo); "
        f"staging ma teraz {len(merged)} unikalnych URL-i."
    )
    print("👉 Możesz teraz uruchomić: python -m malta_housing parse")
    return [item.model_dump() for item in scraped_data]


if __name__ == "__main__":
    run_propertymarket_scraper(max_pages=3)
