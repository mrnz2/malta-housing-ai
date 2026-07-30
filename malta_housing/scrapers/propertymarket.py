"""Property Market Malta scraper — merges results into scraped_listings.json."""

from __future__ import annotations

import random
import re
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
# mnp/mxp ≈ €150k–€190k on this portal's price index scale.
_PRICE_FILTER = "mnp=15&mxp=19"
# Malta locality IDs from portal search (excludes Gozo).
_LOCATION_IDS = (
    "131,132,133,134,135,136,137,138,139,140,141,142,143,144,145,146,147,148,149,"
    "150,151,152,153,174,175,176,178,179,180,182,184,185,186,187,188,189,190,191,192,"
    "193,194,195,196,197,198,199,200,201,202,203,204,205,206,207,208,209,210,211,212,"
    "213,214,215,219,216,217,218,220,221,222,223,250,224,225,226,227,228,229,230,231,"
    "232,233,234,235,236,237,238,239,240,241,242,243,244,245,246,247,248,249"
)
SOURCE = "propertymarket"

_LISTING_PRICE_EUR_RE = re.compile(r"€\s*([\d,]+)")
_PRICE_HEADER_RE = re.compile(r"^Price:\s*(.+)$", re.M | re.I)

# SiteGround WAF allows ~2 listing index pages per auth cookie. Beyond that we rotate
# sort order (new search) and start a fresh session with a cooldown.
MAX_PAGES_PER_SESSION = 2
SESSION_COOLDOWN_SEC = 45.0
SEARCH_PROFILES: tuple[tuple[str, str], ...] = (
    ("najnowsze", "1"),
    ("najtansze", "2"),
    ("najdrozsze", "3"),
)

HEADERS = {
    **DEFAULT_HEADERS,
    "Referer": f"{BASE_URL}/",
}

def _search_query(*, sort_code: str, page_num: int) -> str:
    return (
        f"li={_LOCATION_IDS}&{_PRICE_FILTER}&pc=0&pt=0&nb=0"
        f"&o={sort_code}&d=0&f=&pp={page_num}"
    )


def _abs_url(href: str) -> str:
    return href if href.startswith("http") else BASE_URL + href


def _page_url(page_num: int, *, sort_code: str = "1") -> str:
    return f"{SEARCH_URL}?{_search_query(sort_code=sort_code, page_num=page_num)}"


def _pagination_url(soup: BeautifulSoup, page_num: int) -> str | None:
    pat = re.compile(rf"(?:\?|&)pp={page_num}(?:&|$)")
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if "/for-sale/" in href and pat.search(href):
            return _abs_url(href)
    return None


def _next_page_url(soup: BeautifulSoup) -> str | None:
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        text = (a_tag.get_text() or "").strip()
        if "/for-sale/" in href and "pp=" in href and text == ">":
            return _abs_url(href)
    return None


def _normalize_listing_url(href: str) -> str | None:
    href = href.strip()
    if "/view/" not in href:
        return None
    full_url = _abs_url(href)
    full_url = full_url.split("#", 1)[0].split("?", 1)[0]
    if "/view/" not in full_url:
        return None
    # Detail pages 403 without a trailing slash.
    if not full_url.endswith("/"):
        full_url += "/"
    return full_url


def _extract_listing_links(soup: BeautifulSoup) -> list[str]:
    links: set[str] = set()
    for listing_div in soup.find_all("div", class_="searchResultListing"):
        for a_tag in listing_div.find_all("a", href=True):
            normalized = _normalize_listing_url(a_tag["href"])
            if normalized:
                links.add(normalized)
    return list(links)


def _is_blocked_response(response) -> bool:
    """Detect SiteGround bot challenge / empty challenge HTML."""
    if response.status_code in {202, 403, 429}:
        return True
    headers = {k.lower(): v for k, v in response.headers.items()}
    if headers.get("sg-captcha"):
        return True
    text = response.text or ""
    return "sgcaptcha" in text or "Robot Challenge Screen" in text


def _warm_session(client: HttpClient, referer: str | None = None) -> str:
    """Solve PoW if needed and return a listing index URL usable as Referer."""
    referer = referer or f"{BASE_URL}/"
    landing = _page_url(1)
    client.get(landing, referer=referer)
    return landing


def _fetch_listing_index(
    client: HttpClient,
    page_num: int,
    *,
    sort_code: str,
    referer: str,
    prior_soup: BeautifulSoup | None = None,
) -> tuple[list[str], BeautifulSoup | None, str | None]:
    """Fetch one search-results page; prefer exact pagination hrefs from prior HTML."""
    if page_num <= 1:
        url = _page_url(1, sort_code=sort_code)
    elif prior_soup is not None:
        url = _next_page_url(prior_soup) or _pagination_url(prior_soup, page_num)
        if not url:
            url = _page_url(page_num, sort_code=sort_code)
    else:
        url = _page_url(page_num, sort_code=sort_code)

    print(f"🔎 [Property Market] Skanowanie strony {page_num}: {url}")

    try:
        response = client.get(url, referer=referer)
    except Exception as e:
        print(f"   └─ Błąd podczas pobierania strony {page_num}: {e}")
        return [], None, None

    if _is_blocked_response(response):
        print(
            f"   └─ Blokada SiteGround (HTTP {response.status_code}). "
            "Spróbuj ponownie za chwilę lub zmniejsz częstotliwość zapytań."
        )
        return [], None, None

    soup = BeautifulSoup(response.text, "html.parser")
    links = _extract_listing_links(soup)
    print(f"   └─ Znaleziono {len(links)} unikalnych ogłoszeń na stronie {page_num}.")
    return links, soup, url


def _parse_eur_amount(text: str) -> int | None:
    match = _LISTING_PRICE_EUR_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _listing_price_text(soup: BeautifulSoup) -> str | None:
    price_el = soup.select_one("#myListingDetailsPrice")
    if not price_el:
        return None
    text = price_el.get_text(" ", strip=True)
    return text or None


def listing_price_eur_from_html(html: str) -> int | None:
    """Read listing price from #myListingDetailsPrice (Property Market detail pages)."""
    soup = BeautifulSoup(html, "html.parser")
    price_text = _listing_price_text(soup)
    if not price_text:
        return None
    return _parse_eur_amount(price_text)


def listing_price_eur_from_raw_text(raw_text: str) -> int | None:
    """Read price from a leading 'Price: …' line added by parse_propertymarket_html."""
    for line in raw_text.splitlines():
        match = _PRICE_HEADER_RE.match(line.strip())
        if match:
            return _parse_eur_amount(match.group(1))
    return None


def apply_propertymarket_price_correction(
    parsed: dict,
    raw_text: str,
    *,
    html: str | None = None,
) -> dict:
    """Override LLM price with the portal's explicit listing price when available."""
    price = listing_price_eur_from_html(html) if html else None
    if price is None:
        price = listing_price_eur_from_raw_text(raw_text)
    if price is None:
        return parsed
    updated = dict(parsed)
    updated["price_eur"] = price
    return updated


def _strip_listing_noise(soup: BeautifulSoup) -> None:
    """Remove mortgage calculator, sponsored blocks, and similar non-listing content."""
    for tag in soup.find_all(id=re.compile(r"mortgage", re.I)):
        tag.decompose()

    for tag in soup.find_all(class_=re.compile(r"mortgage|sponsored|advert|adsbygoogle", re.I)):
        tag.decompose()

    for heading in soup.find_all(["h2", "h3", "h4"]):
        text = heading.get_text(" ", strip=True).lower()
        if text in {"mortgage calculator", "sponsored"}:
            block = heading.find_parent(["div", "section", "aside", "form"])
            if block is not None:
                block.decompose()
            else:
                heading.decompose()


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
    for element in main.find_all(class_=re.compile(r"advert|adsbygoogle|promo|banner|sponsor", re.I)):
        element.decompose()
    return main.get_text(separator="\n", strip=True)


def parse_propertymarket_html(html: str, url: str) -> ScrapedListing:
    """Extract a staging listing from Property Market detail page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    _strip_listing_noise(soup)

    main = soup.find("main")

    # Price and title live inside <header> within <main>; read them before
    # _main_content_text() strips header nodes from the tree.
    price_text = _listing_price_text(soup)

    title_tag = soup.select_one("#myListingDetailsTitle h1")
    if not title_tag and main:
        title_tag = main.select_one("#myListingDetailsTitle h1") or main.find("h1")
    if title_tag:
        title = title_tag.get_text(strip=True)
    else:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = str(og_title["content"]).strip()
        else:
            page_title = soup.find("title")
            title = page_title.get_text(strip=True) if page_title else "Brak tytułu"

    if title == "Brak tytułu":
        title = "Property Market Listing"

    meta_text = "\n".join(_meta_lines(soup))
    main_text = _main_content_text(main) if main else ""

    header_lines = [
        f"Title: {title}",
        f"URL: {url}",
    ]
    if price_text:
        header_lines.append(f"Price: {price_text}")

    parts = [part for part in ("\n".join(header_lines), meta_text, main_text) if part]
    raw_text = "\n\n".join(parts)

    if not raw_text.strip():
        title, raw_text = extract_title_and_text(html)
        if price_text:
            raw_text = f"Price: {price_text}\n\n{raw_text}"

    return ScrapedListing(
        url=url,
        title=title,
        raw_text=raw_text,
        source=SOURCE,
        scraped_at=utc_now_iso(),
    )


def scrape_item_details(
    client: HttpClient, url: str, *, referer: str | None = None
) -> ScrapedListing | None:
    """Pobiera treść pojedynczego ogłoszenia z Property Market."""
    listing_referer = referer or _page_url(1)

    for attempt in range(1, 4):
        try:
            response = client.get(url, referer=listing_referer)
        except Exception as e:
            if attempt >= 3:
                print(f"   └─ Błąd pobierania {url}: {e}")
                return None
            print("   └─ 403/timeout — ponawiam po przerwie…")
            client.clear_cookies()
            time.sleep(12.0 * attempt)
            listing_referer = _warm_session(client, referer=f"{BASE_URL}/")
            continue

        if _is_blocked_response(response):
            if attempt >= 3:
                print(f"   └─ Blokada SiteGround przy {url}")
                return None
            print("   └─ Blokada — ponawiam po przerwie…")
            client.clear_cookies()
            time.sleep(12.0 * attempt)
            listing_referer = _warm_session(client, referer=f"{BASE_URL}/")
            continue

        return parse_propertymarket_html(response.text, url)

    return None


def _listing_page_plan(max_pages: int) -> list[tuple[str, str, int]]:
    """Map requested pages onto (profile label, sort code, page number) triples."""
    plan: list[tuple[str, str, int]] = []
    pages_left = max_pages
    profile_idx = 0
    while pages_left > 0 and profile_idx < len(SEARCH_PROFILES):
        label, sort_code = SEARCH_PROFILES[profile_idx]
        batch = min(MAX_PAGES_PER_SESSION, pages_left)
        for page_num in range(1, batch + 1):
            plan.append((label, sort_code, page_num))
        pages_left -= batch
        profile_idx += 1
    return plan


def run_propertymarket_scraper(max_pages: int = 3) -> list[dict]:
    """Główna funkcja uruchamiająca pobieranie z Property Market Malta."""
    # TLS impersonation + automatic SiteGround PoW solve (HTTP 202 → _I_ cookie).
    client = HttpClient(
        headers=HEADERS,
        impersonate="chrome124",
        timeout=25.0,
        max_retries=4,
    )
    all_item_urls: set[str] = set()
    plan = _listing_page_plan(max_pages)

    if max_pages > MAX_PAGES_PER_SESSION:
        print(
            "ℹ️  Property Market (SiteGround) zwykle pozwala na ~2 strony wyników na sesję.\n"
            f"   Dla --pages {max_pages} scraper rotuje sortowanie "
            f"({', '.join(label for label, _ in SEARCH_PROFILES)}) "
            "i odczekuje między sesjami.\n"
        )

    print(
        f"🚀 Rozpoczynam pobieranie z Property Market "
        f"({len(plan)} przejść po stronach wyników)...\n"
    )

    last_listing_url = f"{BASE_URL}/"
    current_sort = ""
    prior_soup: BeautifulSoup | None = None

    for step_idx, (profile_label, sort_code, page_num) in enumerate(plan):
        if sort_code != current_sort:
            if current_sort:
                wait = SESSION_COOLDOWN_SEC + random.uniform(0, 15)
                print(
                    f"⏳ Przerwa {wait:.0f}s — nowa sesja (sortowanie: {profile_label})…"
                )
                time.sleep(wait)
                client.clear_cookies()
            current_sort = sort_code
            prior_soup = None
            last_listing_url = f"{BASE_URL}/"

        links, prior_soup, fetched_url = _fetch_listing_index(
            client,
            page_num,
            sort_code=sort_code,
            referer=last_listing_url,
            prior_soup=prior_soup,
        )
        if fetched_url:
            last_listing_url = fetched_url
        all_item_urls.update(links)

        if step_idx + 1 < len(plan):
            time.sleep(random.uniform(3.0, 5.0))

    print(f"\n📊 Łącznie zebrano {len(all_item_urls)} unikalnych ofert z Property Market.\n")

    scraped_data: list[ScrapedListing] = []
    skipped_gozo = 0
    for i, url in enumerate(sorted(all_item_urls), 1):
        print(f"[{i}/{len(all_item_urls)}] Pobieranie opisu: {url}")
        item_data = scrape_item_details(client, url, referer=last_listing_url)
        if item_data:
            if is_gozo_listing(title=item_data.title, url=item_data.url):
                skipped_gozo += 1
                print("   └─ Pominięto (Gozo).")
            else:
                scraped_data.append(item_data)
        time.sleep(random.uniform(3.0, 5.0))

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
