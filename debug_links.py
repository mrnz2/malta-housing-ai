import requests
from bs4 import BeautifulSoup

URL = "https://ownersbest.com.mt/malta-properties/?prop_tag=sale&pg=1"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

response = requests.get(URL, headers=HEADERS, timeout=10)
soup = BeautifulSoup(response.text, "html.parser")

# Wyciągamy absolutne i względne linki, ignorując kotwice/javascript
all_links = set()
for a in soup.find_all("a", href=True):
    href = a["href"].strip()
    if href and not href.startswith(("#", "javascript:")):
        all_links.add(href)

print(f"🔎 Znaleziono {len(all_links)} unikalnych linków.\n")
print("--- UNIKALNE LINKI (próbka): ---")
for link in sorted(list(all_links)):
    # Wyświetlamy linki, które wyglądają jak podstrony nieruchomości lub podstrony witryny
    if "ownersbest.com.mt" in link or link.startswith("/") or "?" in link:
        print(f" -> {link}")