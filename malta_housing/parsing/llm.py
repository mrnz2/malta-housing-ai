"""LLM parser with retries, checkpoints, failure logging, and known-URL skip."""

from __future__ import annotations

import json
import re
from typing import Any

import ollama

from malta_housing.budget import is_out_of_budget
from malta_housing.common import (
    PARSE_FAILURES_PATH,
    PARSED_PATH,
    STAGING_PATH,
    append_jsonl,
    load_json_list,
    resolve_source,
    save_json_list,
)
from malta_housing.db.store import get_known_urls
from malta_housing.distances import distance_to_gzira_km
from malta_housing.geo import is_gozo_record
from malta_housing.models import MaltaPropertySchema, ParsedListing, utc_now_iso

CHECKPOINT_EVERY = 5
LLM_RETRIES = 3
MODEL_NAME = "qwen2.5:7b"


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
    - locality: Tylko miejscowość na Malcie (wyspa główna). Jeśli oferta jest na Gozo, i tak wpisz nazwę miejscowości (np. "Xewkija (Gozo)") — filtr Gozo działa osobno.
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

    last_error: Exception | None = None
    for attempt in range(1, LLM_RETRIES + 1):
        try:
            response = ollama.chat(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                format="json",
            )
            json_data = json.loads(response["message"]["content"])
            return MaltaPropertySchema(**json_data)
        except Exception as exc:
            last_error = exc
            print(f"   └─ Próba {attempt}/{LLM_RETRIES} nieudana: {exc}")
    raise RuntimeError(f"LLM parse failed after {LLM_RETRIES} attempts: {last_error}")


def _quality_null_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "missing_price_eur": sum(1 for i in items if i.get("price_eur") is None),
        "missing_locality": sum(1 for i in items if not i.get("locality")),
    }


def run_parser(
    *,
    force: bool = False,
    checkpoint_every: int = CHECKPOINT_EVERY,
) -> list[dict[str, Any]]:
    raw_listings = load_json_list(STAGING_PATH)
    if not raw_listings:
        print(f"⚠️ Brak danych w {STAGING_PATH}.")
        return []

    already_parsed = {
        item["url"]: item
        for item in load_json_list(PARSED_PATH)
        if "url" in item and not is_gozo_record(item) and not is_out_of_budget(item)
    }
    # Backfill distance on checkpoint rows that predate this field
    for url, item in list(already_parsed.items()):
        if item.get("distance_to_gzira_km") is None and item.get("locality"):
            km = distance_to_gzira_km(item.get("locality"))
            if km is not None:
                item = dict(item)
                item["distance_to_gzira_km"] = km
                already_parsed[url] = item
    known_db_urls = set() if force else get_known_urls()

    to_process: list[dict[str, Any]] = []
    skipped_known = 0
    skipped_gozo = 0
    skipped_budget = 0
    for item in raw_listings:
        url = item.get("url")
        if not url:
            continue
        if is_gozo_record(item):
            skipped_gozo += 1
            continue
        if not force and url in known_db_urls:
            skipped_known += 1
            continue
        if not force and url in already_parsed:
            continue
        to_process.append(item)

    print(
        f"🚀 Parsowanie: {len(to_process)} do zrobienia "
        f"(pominięto {skipped_known} już w DB, "
        f"{skipped_gozo} Gozo, "
        f"{len(already_parsed)} już w parsed checkpoint).\n"
    )

    results_by_url = dict(already_parsed)
    success = 0
    failures = 0

    for i, item in enumerate(to_process, 1):
        print(f"[{i}/{len(to_process)}] Parsowanie: {item.get('title', item['url'])}...")
        try:
            parsed_data = parse_with_llm(item)
            result = ParsedListing(
                **parsed_data.model_dump(),
                url=item["url"],
                source=resolve_source(item.get("source"), item["url"]),
                scraped_at=item.get("scraped_at"),
                updated_at=utc_now_iso(),
                distance_to_gzira_km=distance_to_gzira_km(parsed_data.locality),
            )
            result_dict = result.model_dump()
            if is_gozo_record(result_dict):
                skipped_gozo += 1
                print("   └─ Pominięto (Gozo).")
                continue
            if is_out_of_budget(result_dict):
                skipped_budget += 1
                print("   └─ Pominięto (poza budżetem).")
                continue
            results_by_url[item["url"]] = result_dict
            success += 1
            dist = result_dict.get("distance_to_gzira_km")
            dist_txt = f", →Gżira: {dist} km" if dist is not None else ""
            print(
                f"   └─ Sukces! Cena: €{parsed_data.price_eur}, "
                f"Sprzedawca: {parsed_data.seller_type}, "
                f"Freehold: {parsed_data.is_freehold}, Airspace: {parsed_data.has_airspace}"
                f"{dist_txt}"
            )
        except Exception as e:
            failures += 1
            append_jsonl(
                PARSE_FAILURES_PATH,
                {
                    "url": item.get("url"),
                    "title": item.get("title"),
                    "source": item.get("source"),
                    "error": str(e),
                    "failed_at": utc_now_iso(),
                },
            )
            print(f"   └─ Błąd parsowania (zapisano do {PARSE_FAILURES_PATH}): {e}")

        if i % checkpoint_every == 0 or i == len(to_process):
            save_json_list(PARSED_PATH, list(results_by_url.values()))
            print(f"   💾 Checkpoint: {len(results_by_url)} rekordów w {PARSED_PATH}")

    all_results = list(results_by_url.values())
    save_json_list(PARSED_PATH, all_results)

    nulls = _quality_null_counts(all_results)
    print(
        f"\n✅ Gotowe! Sukces w tej sesji: {success}, błędy: {failures}, "
        f"pominięte Gozo: {skipped_gozo}, poza budżetem: {skipped_budget}. "
        f"Łącznie w {PARSED_PATH}: {len(all_results)}. "
        f"Nulls — price: {nulls['missing_price_eur']}, locality: {nulls['missing_locality']}."
    )
    return all_results
