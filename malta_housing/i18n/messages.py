"""Lookup tables for render-time translation of fixed codes."""

from __future__ import annotations

from typing import Any

Locale = str

_MESSAGES: dict[str, dict[str, dict[str, str]]] = {
    "sea_proximity": {
        "nad_morzem": {"en": "Seafront", "pl": "Nad morzem"},
        "blisko": {"en": "Near sea", "pl": "Blisko morza"},
        "daleko": {"en": "Inland", "pl": "Daleko od morza"},
    },
    "seller_type": {
        "OWNER": {"en": "Owner", "pl": "Właściciel"},
        "AGENT": {"en": "Agent", "pl": "Agent"},
        "SENSAR": {"en": "Sensar broker", "pl": "Broker Sensar"},
        "UNKNOWN": {"en": "Unknown", "pl": "Nieznany"},
    },
    "source": {
        "maltapark": {"en": "Maltapark", "pl": "Maltapark"},
        "ownersbest": {"en": "Owners Best", "pl": "Owners Best"},
        "djar": {"en": "Djar", "pl": "Djar"},
        "propertymarket": {"en": "Property Market", "pl": "Property Market"},
        "yitaku": {"en": "Yitaku", "pl": "Yitaku"},
        "remax": {"en": "RE/MAX", "pl": "RE/MAX"},
        "simonmamo": {"en": "Simon Mamo", "pl": "Simon Mamo"},
        "belair": {"en": "Belair", "pl": "Belair"},
        "re316": {"en": "316 Real Estate", "pl": "316 Real Estate"},
        "franksalt": {"en": "Frank Salt", "pl": "Frank Salt"},
        "sensar": {"en": "Sensara Malta", "pl": "Sensara Malta"},
        "excelhomes": {"en": "Excel Homes", "pl": "Excel Homes"},
        "dhalia": {"en": "Dhalia", "pl": "Dhalia"},
        "alliance": {"en": "Alliance", "pl": "Alliance"},
    },
    "bank_risk": {
        "low": {"en": "Low", "pl": "Niskie"},
        "medium": {"en": "Medium", "pl": "Średnie"},
        "high": {"en": "High", "pl": "Wysokie"},
        "niskie": {"en": "Low", "pl": "Niskie"},
        "średnie": {"en": "Medium", "pl": "Średnie"},
        "wysokie": {"en": "High", "pl": "Wysokie"},
    },
    "confidence": {
        "low": {"en": "Low", "pl": "Niska"},
        "medium": {"en": "Medium", "pl": "Średnia"},
    },
    "score_breakdown": {
        "value_vs_market": {"en": "Value vs market", "pl": "Wartość vs rynek"},
        "location_quality": {"en": "Location", "pl": "Lokalizacja"},
        "property_fit": {"en": "Property fit", "pl": "Dopasowanie"},
        "finish_amenities": {"en": "Finish & amenities", "pl": "Wykończenie i udogodnienia"},
        "legal_safety": {"en": "Legal safety", "pl": "Bezpieczeństwo prawne"},
        "price_per_sqm": {"en": "Price/m²", "pl": "Cena/m²"},
        "distance_to_gzira": {"en": "Distance to Gżira", "pl": "Odległość do Gżiry"},
        "sea_proximity": {"en": "Sea proximity", "pl": "Bliskość morza"},
        "area_sqm": {"en": "Area", "pl": "Metraż"},
        "property_flags": {"en": "Property flags", "pl": "Cechy nieruchomości"},
    },
    "finish_level": {
        "shell": {"en": "Shell", "pl": "Shell"},
        "finished": {"en": "Finished", "pl": "Wykończone"},
        "furnished": {"en": "Furnished", "pl": "Umeblowane"},
        "needs_renovation": {"en": "Needs renovation", "pl": "Wymaga remontu"},
    },
    "micro_zone": {
        "seafront": {"en": "Seafront", "pl": "Nad morzem"},
        "premium": {"en": "Premium", "pl": "Premium"},
        "standard": {"en": "Standard", "pl": "Standard"},
        "quiet": {"en": "Quiet", "pl": "Cicha okolica"},
    },
    "ground_rent_type": {
        "temporary": {"en": "Temporary", "pl": "Tymczasowy"},
        "perpetual": {"en": "Perpetual", "pl": "Wieczysty"},
    },
    "sanity_flag": {
        "area_heuristic": {
            "en": "Internal area estimated as 85% of total (heuristic)",
            "pl": "Internal oszacowany jako 85% total area (heurystyka)",
        },
        "missing_internal_area": {
            "en": "No internal area — valuation unreliable",
            "pl": "Brak metrażu internal — wycena niewiarygodna",
        },
        "price_area_mismatch": {
            "en": "Area likely wrong or price covers share/garage/separate unit",
            "pl": "Metraż prawdopodobnie zawiera błąd lub cena dotyczy udziału/garażu/osobnej części",
        },
        "optional_garage_excluded": {
            "en": "Optional garage at extra cost — not included in flat valuation",
            "pl": "Garaż opcjonalny za dopłatą — nie wliczony w wycenę mieszkania",
        },
    },
    "property_type": {
        "apartment": {"en": "Apartment", "pl": "Mieszkanie"},
        "maisonette": {"en": "Maisonette", "pl": "Maisonette"},
        "penthouse": {"en": "Penthouse", "pl": "Penthouse"},
        "garage": {"en": "Garage", "pl": "Garaż"},
        "town_house": {"en": "Town house", "pl": "Kamienica"},
        "terraced_house": {"en": "Terraced house", "pl": "Dom szeregowy"},
        "house_of_character": {"en": "House of character", "pl": "Kamienica charakteru"},
        "studio": {"en": "Studio", "pl": "Kawalerka"},
        "land": {"en": "Land", "pl": "Działka"},
        "villa": {"en": "Villa", "pl": "Willa"},
        "duplex": {"en": "Duplex", "pl": "Duplex"},
        "business": {"en": "Commercial", "pl": "Lokal użytkowy"},
        "other": {"en": "Other", "pl": "Inne"},
    },
    "bool_yes": {"true": {"en": "Yes", "pl": "Tak"}, "false": {"en": "No", "pl": "Nie"}},
    "bool_unknown": {"null": {"en": "?", "pl": "?"}},
}

# Legacy Polish sanity flag sentences → codes
_LEGACY_SANITY_FLAG_MAP: dict[str, str] = {
    "internal oszacowany jako 85% total area (heurystyka)": "area_heuristic",
    "brak metrażu internal — wycena niewiarygodna": "missing_internal_area",
    "metraż prawdopodobnie zawiera błąd lub cena dotyczy udziału/garażu/osobnej części": "price_area_mismatch",
    "garaż opcjonalny za dopłatą — nie wliczony w wycenę mieszkania": "optional_garage_excluded",
}

_LEGACY_BANK_RISK_MAP: dict[str, str] = {
    "niskie": "low",
    "średnie": "medium",
    "wysokie": "high",
    "low": "low",
    "medium": "medium",
    "high": "high",
}


def normalize_locale(locale: str | None) -> Locale:
    if not locale:
        return "en"
    code = str(locale).strip().lower()[:2]
    return code if code in {"en", "pl"} else "en"


def label(category: str, code: str | None, locale: str | None) -> str:
    """Return a localized label for a fixed code, or the code itself as fallback."""
    loc = normalize_locale(locale)
    if not code:
        return "—"
    table = _MESSAGES.get(category, {})
    key = str(code).strip()
    entry = table.get(key) or table.get(key.lower()) or table.get(key.upper())
    if entry:
        return entry.get(loc) or entry.get("en") or key
    return key


def localize_code(category: str, code: str | None, locale: str | None) -> str:
    return label(category, code, locale)


def normalize_bank_risk(value: str | None) -> str:
    if not value:
        return "medium"
    key = str(value).strip().lower()
    return _LEGACY_BANK_RISK_MAP.get(key, key if key in {"low", "medium", "high"} else "medium")


def normalize_sanity_flag(value: str) -> str:
    key = str(value).strip().lower()
    if key in _MESSAGES["sanity_flag"]:
        return key
    return _LEGACY_SANITY_FLAG_MAP.get(key, value)


def localize_sanity_flags(flags: list[Any] | None, locale: str | None) -> list[str]:
    loc = normalize_locale(locale)
    result: list[str] = []
    for item in flags or []:
        code = normalize_sanity_flag(str(item))
        table = _MESSAGES["sanity_flag"]
        if code in table:
            result.append(table[code].get(loc) or table[code]["en"])
        else:
            result.append(str(item))
    return result


def localize_score_breakdown(
    breakdown: dict[str, Any] | None, locale: str | None
) -> dict[str, float] | None:
    if not isinstance(breakdown, dict):
        return None
    loc = normalize_locale(locale)
    table = _MESSAGES["score_breakdown"]
    localized: dict[str, float] = {}
    for key, value in breakdown.items():
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        label_key = table.get(key, {}).get(loc) or table.get(key, {}).get("en") or key
        localized[label_key] = num
    return localized


def localize_bank_valuation(bank: dict[str, Any] | None, locale: str | None) -> dict[str, Any] | None:
    if not isinstance(bank, dict):
        return None
    loc = normalize_locale(locale)
    out = dict(bank)
    risk_code = normalize_bank_risk(bank.get("bank_risk"))
    out["bank_risk"] = risk_code
    out["bank_risk_label"] = label("bank_risk", risk_code, loc)
    conf = str(bank.get("confidence") or "medium").lower()
    out["confidence_label"] = label("confidence", conf, loc)
    out["sanity_flags"] = localize_sanity_flags(bank.get("sanity_flags"), loc)
    return out
