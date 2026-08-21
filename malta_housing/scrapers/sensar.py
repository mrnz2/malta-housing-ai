"""Sensara Malta scraper — browser list + HttpClient details → scraped_listings.json."""

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

# Detail pages accept curl_cffi; Archivio AJAX is Cloudflare-blocked without a real browser.
HEADERS = {
    "Referer": f"{BASE_URL}/en/sales/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_LISTING_HREF_RE = re.compile(r"/en/sales/V[^\s\"'<>#?]+", re.I)


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


def _links_from_hrefs(hrefs: list[str]) -> list[str]:
    links: set[str] = set()
    for href in hrefs:
        if not href:
            continue
        normalized = _normalize_listing_url(href)
        if normalized:
            links.add(normalized)
    return sorted(links)


def _launch_chromium(playwright):  # type: ignore[no-untyped-def]
    """Prefer installed Chrome/Edge (better Cloudflare pass rate), else bundled Chromium."""
    last_error: Exception | None = None
    for kwargs in (
        {"channel": "chrome", "headless": True},
        {"channel": "msedge", "headless": True},
        {"headless": True},
    ):
        try:
            return playwright.chromium.launch(**kwargs)
        except Exception as exc:  # noqa: BLE001 — try next launch mode
            last_error = exc
    raise RuntimeError(f"Could not launch Chromium/Chrome/Edge: {last_error}")


def _wait_for_listings(page) -> None:  # type: ignore[no-untyped-def]
    """Wait out Cloudflare interstitial, then for listing cards."""
    try:
        page.wait_for_function(
            "() => !/just a moment|attention required/i.test(document.title)",
            timeout=60_000,
        )
    except Exception:
        pass
    page.wait_for_selector("a.annuncio[href*='/en/sales/V']", timeout=45_000)


def fetch_listing_links_playwright(max_pages: int) -> list[str]:
    """Load sales pages in a real browser (site JS + CF), collect property URLs."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Sensar wymaga pakietu playwright (Cloudflare blokuje zwykły AJAX).\n"
            "  .\\venv\\Scripts\\pip.exe install playwright\n"
            "  .\\venv\\Scripts\\python.exe -m playwright install chromium"
        ) from exc

    print(f"🧭 [Sensar] Lista przez przeglądarkę (strony 1-{max_pages})…")
    all_links: set[str] = set()

    with sync_playwright() as p:
        browser = _launch_chromium(p)
        context = browser.new_context(
            locale="en-US",
            viewport={"width": 1365, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        try:
            for page_num in range(1, max_pages + 1):
                url = SEARCH_PAGE_URL.format(page=page_num)
                print(f"🔎 [Sensar] Strona {page_num}: {url}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    _wait_for_listings(page)
                    # Site injects cards via Archivio AJAX after load.
                    page.wait_for_timeout(800)
                    hrefs = page.eval_on_selector_all(
                        "a.annuncio[href*='/en/sales/V']",
                        "els => els.map(e => e.getAttribute('href') || '')",
                    )
                    links = _links_from_hrefs(list(hrefs) if isinstance(hrefs, list) else [])
                except Exception as e:
                    print(f"   └─ Błąd strony {page_num}: {e}")
                    if page_num == 1:
                        break
                    print("   └─ Koniec paginacji.")
                    break

                print(f"   └─ Znaleziono {len(links)} ogłoszeń.")
                if not links:
                    print("   └─ Pusta strona — koniec paginacji.")
                    break
                all_links.update(links)
                if page_num < max_pages:
                    time.sleep(random.uniform(0.6, 1.2))
        finally:
            context.close()
            browser.close()

    return sorted(all_links)


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
    print(f"🚀 Rozpoczynam pobieranie z Sensara Malta (strony 1-{max_pages})...\n")

    try:
        all_item_urls = set(fetch_listing_links_playwright(max_pages))
    except RuntimeError as e:
        print(f"❌ {e}")
        return []

    print(f"\n📊 Łącznie zebrano {len(all_item_urls)} unikalnych ofert z Sensara Malta.\n")

    client = HttpClient(headers=HEADERS, impersonate="chrome124", timeout=25.0)
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
