import json
import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator
import ollama

# 1. Definiujemy ścisłą strukturę danych z odpornością na wartości null w booleanach
class MaltaPropertySchema(BaseModel):
    title: str = Field(description="Tytuł nieruchomości")
    price_eur: Optional[int] = Field(description="Cena w EUR jako czysta liczba, np. 650000. Szukaj kwot przy symbolu €")
    locality: Optional[str] = Field(description="Miejscowość/miasto (np. Sliema, Qala, Santa Venera, Munxar, Birzebbuga)")
    property_type: Optional[str] = Field(description="Typ: Apartment, Maisonette, Townhouse, Garage, Terraced House itp.")
    bedrooms: Optional[int] = Field(description="Liczba sypialni (int)")
    seller_type: Optional[str] = Field(description="Wybierz dokładnie jeden: OWNER, AGENT, SENSAR, UNKNOWN")
    
    # Maltańskie słowa kluczowe
    is_freehold: bool = Field(default=False, description="True tylko jeśli w tekście pojawia się słowo Freehold")
    has_airspace: bool = Field(default=False, description="True tylko jeśli w tekście pojawia się Airspace")
    has_sea_view: bool = Field(default=False, description="True jeśli pojawia się Sea View / Valley View / Breathtaking Views")
    is_shell_form: bool = Field(default=False, description="True tylko jeśli Level of Finish to Shell")
    
    key_features: list[str] = Field(default=[], description="Max 4 najważniejsze atuty nieruchomości")

    # Walidator zamieniający ewentualny null/None z LLM na False
    @field_validator("is_freehold", "has_airspace", "has_sea_view", "is_shell_form", mode="before")
    @classmethod
    def convert_null_to_bool(cls, v):
        if v is None:
            return False
        return bool(v)

def clean_raw_text(text: str) -> str:
    """Zachowujemy nagłówek z ceną i typem sprzedawcy, obcinamy tylko dolną nawigację."""
    end_match = re.search(r"Featured Listings|Recently Viewed|Install the Maltapark app", text)
    end_idx = end_match.start() if end_match else len(text)
    return text[:end_idx].strip()

def parse_with_llm(raw_listing: dict) -> MaltaPropertySchema:
    cleaned_text = clean_raw_text(raw_listing["raw_text"])
    
    prompt = f"""
    Jesteś analitykiem rynku nieruchomości na Malcie. Wyciągnij precyzyjne dane z poniższego ogłoszenia.

    REGUŁY EXTRACKCJI:
    - price_eur: Zawsze wyciągaj pełną kwotę w euro (np. dla "€ 650,000" wpisz 650000). Jeśli jest podana opcjonalna cena garażu, ignoruj ją i weź główną cenę nieruchomości.
    - seller_type: Jeśli widzisz "OWNER" wpisz "OWNER". Jeśli "AGENT" lub nazwy agencji (np. ReMax) wpisz "AGENT". Jeśli "BROKER (SENSAR)" wpisz "SENSAR".
    - Wartości boolean (is_freehold, has_airspace, has_sea_view, is_shell_form) MUSZĄ być ustawione na true lub false (NIGDY null).

    SUROWY TEKST OGŁOSZENIA:
    ---
    Tytuł: {raw_listing['title']}
    URL: {raw_listing['url']}
    {cleaned_text}
    ---

    Zwróć WYŁĄCZNIE poprawny obiekt JSON zgody ze schematem:
    {{
        "title": "string",
        "price_eur": 650000,
        "locality": "string lub null",
        "property_type": "string lub null",
        "bedrooms": 2,
        "seller_type": "OWNER/AGENT/SENSAR/UNKNOWN",
        "is_freehold": false,
        "has_airspace": false,
        "has_sea_view": true,
        "is_shell_form": false,
        "key_features": ["cecha 1", "cecha 2"]
    }}
    """

    response = ollama.chat(
        model="qwen2.5:7b",
        messages=[{"role": "user", "content": prompt}],
        format="json"
    )

    json_data = json.loads(response["message"]["content"])
    return MaltaPropertySchema(**json_data)

if __name__ == "__main__":
    with open("scraped_listings.json", "r", encoding="utf-8") as f:
        raw_listings = json.load(f)

    parsed_results = []
    print(f"🚀 Rozpocinamy poprawione parsowanie {len(raw_listings)} ogłoszeń...\n")

    for i, item in enumerate(raw_listings, 1):
        print(f"[{i}/{len(raw_listings)}] Parsowanie: {item['title']}...")
        try:
            parsed_data = parse_with_llm(item)
            result_dict = parsed_data.model_dump()
            result_dict["url"] = item["url"]
            parsed_results.append(result_dict)
            print(f"   └─ Sukces! Cena: €{parsed_data.price_eur}, Sprzedawca: {parsed_data.seller_type}, Freehold: {parsed_data.is_freehold}, Airspace: {parsed_data.has_airspace}")
        except Exception as e:
            print(f"   └─ Błąd parsowania: {e}")

    with open("parsed_listings.json", "w", encoding="utf-8") as f:
        json.dump(parsed_results, f, indent=2, ensure_ascii=False)

    print("\n✅ Gotowe! Sprawdź wygenerowany plik 'parsed_listings.json'")