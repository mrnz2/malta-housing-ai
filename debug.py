import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

url = "https://www.maltapark.com/property/forsale"

print(f"Pobieram: {url}")
response = httpx.get(url, headers=HEADERS, follow_redirects=True)

print(f"Kod odpowiedzi: {response.status_code}")

# Zapisujemy całą odpowiedź HTML do pliku
with open("page_test.html", "w", encoding="utf-8") as f:
    f.write(response.text)

print("Zapisano 'page_test.html'!")