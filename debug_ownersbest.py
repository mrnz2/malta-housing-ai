import requests
from bs4 import BeautifulSoup

URL = "https://ownersbest.com.mt/malta-properties/?prop_tag=sale&pg=1"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

print(f"📡 Pobieranie strony: {URL}")
response = requests.get(URL, headers=HEADERS, timeout=10)

print(f"Status odpowiedzi HTTP: {response.status_code}")
print(f"Długość pobranego HTML: {len(response.text)} znaków\n")

soup = BeautifulSoup(response.text, "html.parser")

all_links = [a["href"] for a in soup.find_all("a", href=True)]
print(f"🔗 Łącznie znaleziono {len(all_links)} linków na stronie.")

print("\n--- PIERWSZE 20 LINKÓW ZE STRONY ---")
for link in all_links[:20]:
    print(f" -> {link}")