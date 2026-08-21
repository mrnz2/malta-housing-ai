"""Sensara Malta scraper — Archivio AJAX merged into scraped_listings.json."""

from __future__ import annotations

import random
import re
import time
from urllib.parse import quote, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from malta_housing.common import HttpClient, extract_title_and_text, load_hidden_urls, merge_staging
from malta_housing.geo import is_gozo_listing
from malta_housing.models import ScrapedListing, utc_now_iso

BASE_URL = "https://www.sensaramalta.com"
SOURCE = "sensar"
_DEFAULT_MAX_PAGES = 5
MAX_PRICE = "400000"

SEARCH_PAGE_URL = f"{BASE_URL}/en/sales/?pr2={MAX_PRICE}&p={{page}}"
AJAX_URL = f"{BASE_URL}/ajax.html?azi=Archivio&lin=en&n={{page}}"

# Form honeypot filled by browser JS before Archivio POST.
_H_UFIELD_HUMAN = "Sono un essere umano. Fidati di quello che faccio"

HEADERS = {
    "Referer": f"{BASE_URL}/en/sales/",
    "Accept": "text/html, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE_URL,
}

_LISTING_HREF_RE = re.compile(r"/en/sales/V[^\s\"'<>#?]+", re.I)


def _archivio_payload(page_num: int) -> dict[str, str]:
    return {
        "H_Url": f"http://www.sensaramalta.com/en/sales/?pr2={MAX_PRICE}&p={page_num}",
        "Src_Li_Tip": "V",
        "Src_Li_Cat": "",
        "Src_Li_Cit": "",
        "Src_Li_Zon": "",
        "Src_T_Pr1": "",
        "Src_T_Pr2": MAX_PRICE,
        "Src_T_Mq1": "",
        "Src_T_Mq2": "",
        "Src_T_Cod": "",
        "Src_Li_Ord": "",
        "T_EField": "",
        "H_UField": _H_UFIELD_HUMAN,
        "CP_C_Funz": "funz",
        "CP_C_Mark": "mark",
        "CP_C_Anal": "anal",
    }


def _normalize_listing_url(href: str) -> str | None:
    href = href.strip()
    match = _LISTING_HREF_RE.search(href)
    if not match:
        return None
    path = match.group(0)
    full_url = path if path.startswith("http") else BASE_URL + path
    return full_url.split("#", 1)[0].split("?", 1)[0]


def _request_url(url: str) -> str:
    """Percent-encode non-ASCII path characters (emoji titles break curl redirects)."""
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme, parts.netloc, quote(parts.path, safe="/"), parts.query, parts.fragment)
    )


def _links_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: set[str] = set()
    for a_tag in soup.find_all("a", href=True):
        normalized = _normalize_listing_url(a_tag["href"])
        if normalized:
            links.add(normalized)
    if not links:
        for match in _LISTING_HREF_RE.finditer(html):
            normalized = _normalize_listing_url(match.group(0))
            if normalized:
                links.add(normalized)
    return sorted(links)


def fetch_listing_links(client: HttpClient, page_num: int) -> list[str]:
    """POST Archivio page and return unique property URLs from the HTML fragment."""
    search_url = SEARCH_PAGE_URL.format(page=page_num)
    ajax_url = AJAX_URL.format(page=page_num)
    print(f"🔎 [Sensar] AJAX strona {page_num}: {search_url}")

    try:
        response = client.session.post(
            ajax_url,
            headers={**HEADERS, "Referer": search_url},
            data=_archivio_payload(page_num),
            timeout=client.timeout,
        )
        response.raise_for_status()
    except Exception as e:
        print(f"   └─ Błąd AJAX strony {page_num}: {e}")
        return []

    html = response.text or ""
    if "Just a moment" in html or "challenge-platform" in html:
        print(f"   └─ Cloudflare zablokował AJAX na stronie {page_num}.")
        return []

    links = _links_from_html(html)
    print(f"   └─ Znaleziono {len(links)} ogłoszeń.")
    return links


def _feature_lines(soup: BeautifulSoup) -> list[str]:
    lines: list[str] = []
    for lab in soup.select("div.lab"):
        label = lab.get_text(" ", strip=True)
        value_el = lab.find_next_sibling("div")
        value = value_el.get_text(" ", strip=True) if value_el else ""
        if label and value:
            lines.append(f"{label}: {value}")
    return lines


def _raw_text_from_detail(html: str, url: str, title: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    price_el = soup.select_one(".prezzo")
    price = price_el.get_text(" ", strip=True) if price_el else None
    subtitle_el = soup.select_one(".comune")
    subtitle = subtitle_el.get_text(" ", strip=True) if subtitle_el else None
    description_el = soup.select_one(".descrizione")
    description = description_el.get_text("\n", strip=True) if description_el else None

    lines = [
        f"Title: {title}",
        f"URL: {url}",
        f"Price: {price or 'n/a'}",
        f"Subtitle: {subtitle or 'n/a'}",
        f"Seller: Sensara Malta (SENSAR)",
    ]
    features = _feature_lines(soup)
    if features:
        lines.extend(["", "Rooms and Features:"])
        lines.extend(features)
    if description:
        lines.extend(["", "Description:", description])
    return "\n".join(lines).strip()


def parse_sensar_html(html: str, url: str) -> ScrapedListing:
    """Extract a staging listing from a Sensara Malta property page."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    if not title:
        title, _ = extract_title_and_text(html)
    if title == "Brak tytułu" or not title:
        title = "Sensara Malta Property"

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
    """Fetch a single Sensara Malta property page."""
    try:
        response = client.get(_request_url(url))
    except Exception as e:
        print(f"   └─ Błąd pobierania {url}: {e}")
        return None

    return parse_sensar_html(response.text, url)


def run_sensar_scraper(max_pages: int = _DEFAULT_MAX_PAGES) -> list[dict]:
    """Fetch up to max_pages from Sensara Malta (default 5), merge into staging."""
    client = HttpClient(headers=HEADERS, impersonate="chrome124", timeout=25.0)
    all_item_urls: set[str] = set()

    print(f"🚀 Rozpoczynam pobieranie z Sensara Malta (strony 1-{max_pages})...\n")

    # Warm session on the search page (cookies / CF bm).
    try:
        client.get(SEARCH_PAGE_URL.format(page=1))
    except Exception as e:
        print(f"   └─ Ostrzeżenie: nie udało się otworzyć listy: {e}")

    for page in range(1, max_pages + 1):
        links = fetch_listing_links(client, page)
        if not links:
            print("   └─ Pusta strona — koniec paginacji.")
            break
        all_item_urls.update(links)
        if page < max_pages:
            time.sleep(random.uniform(0.8, 1.6))

    print(f"\n📊 Łącznie zebrano {len(all_item_urls)} unikalnych ofert z Sensara Malta.\n")

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
    run_sensar_scraper(max_pages=_DEFAULT_MAX_PAGES)
