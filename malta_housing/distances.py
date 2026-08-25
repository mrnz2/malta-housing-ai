"""Locality profiles from to_gzira.csv (Gżira distance, sea proximity, region)."""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal, TypedDict

from malta_housing.geo import _normalize
from malta_housing.paths import PROJECT_ROOT

GZIRA_CSV_PATH = PROJECT_ROOT / "to_gzira.csv"

# Equirectangular extent of map.svg (N 36°06', S 35°47', W 14°10', E 14°36').
MAP_BOUNDS: dict[str, float] = {
    "minLat": 35.7833,
    "maxLat": 36.10,
    "minLng": 14.1667,
    "maxLng": 14.60,
}

SeaProximity = Literal["nad_morzem", "blisko", "daleko"]


class LocalityProfile(TypedDict):
    gzira_km: float
    sea_proximity: SeaProximity | None
    region: str | None
    eur_per_sqm_min: float | None
    eur_per_sqm_max: float | None


class LocalityCoords(TypedDict):
    name: str
    lat: float
    lng: float


# Common scraper/LLM locality spellings → CSV town keys (normalized).
_ALIASES: dict[str, str] = {
    "birzebbugia": "birzebbugia",
    "birzebbuga": "birzebbugia",
    "marsascala": "marsaskala",
    "marsaskala": "marsaskala",
    "st pauls bay": "san pawl il bahar",
    "st paul s bay": "san pawl il bahar",
    "san pawl il bahar": "san pawl il bahar",
    "st julians": "san giljan",
    "st julian s": "san giljan",
    "san giljan": "san giljan",
    "san gwann": "san gwann",
    "gzira": "gzira",
    "hamrun": "hamrun",
    "ghaxaq": "ghaxaq",
    "zebbug malta": "zebbug",
    "zebbug": "zebbug",
    "zabbar": "zabbar",
    "zejtun": "zejtun",
    "zurrieq": "zurrieq",
    "pieta": "pieta",
    "siggiewi": "siggiewi",
    "mellieha": "mellieha",
    "mgarr": "mgarr",
    "cospicua": "bormla",
    "bormla": "bormla",
    "vittoriosa": "birgu",
    "birgu": "birgu",
    "isla": "senglea",
    "senglea": "senglea",
    "xemxija": "san pawl il bahar",
    "qawra": "san pawl il bahar",
    "bugibba": "san pawl il bahar",
    "gillieru": "san pawl il bahar",
    "manikata": "mellieha",
    "swatar": "birkirkara",
    "fleur de lys": "fleur de lys",
    "fleur-de-lys": "fleur de lys",
    "fleurdelys": "fleur de lys",
    "qajjenza": "birzebbugia",
    "zebbiegh": "mgarr",
    "ta xbiex": "ta xbiex",
    "santa lucia": "santa lucija",
    "rabat malta": "rabat",
}


def _parse_eur_per_sqm_range(value: str) -> tuple[float, float] | None:
    """Parse '2900 - 4100' or '2900-4100' into (min, max) EUR/m²."""
    if not value or not str(value).strip():
        return None
    numbers = re.findall(r"[\d]+(?:[.,]\d+)?", str(value).replace(",", ""))
    if len(numbers) < 2:
        return None
    try:
        low = float(numbers[0].replace(",", "."))
        high = float(numbers[1].replace(",", "."))
    except ValueError:
        return None
    if low <= 0 or high <= 0:
        return None
    if low > high:
        low, high = high, low
    return (low, high)


def interpolate_rate(min_rate: float, max_rate: float, position: float) -> float:
    """Linear interpolation within a locality €/m² range (position 0.0–1.0)."""
    pos = max(0.0, min(1.0, position))
    return round(min_rate + (max_rate - min_rate) * pos, 2)


_PENTHOUSE_TYPES = frozenset(
    {"penthouse", "duplex penthouse", "penthouse apartment"}
)
_TOWNHOUSE_TYPES = frozenset(
    {"townhouse", "terraced house", "character house", "house of character", "villa"}
)


def _normalize_property_type(property_type: str | None) -> str:
    if not property_type:
        return ""
    return property_type.strip().lower()


def is_penthouse_or_seafront(property_type: str | None, facts: dict) -> bool:
    ptype = _normalize_property_type(property_type)
    if any(token in ptype for token in ("penthouse", "seafront")):
        return True
    if facts.get("micro_zone") == "seafront":
        return True
    if facts.get("has_sea_view") and facts.get("sea_proximity") == "nad_morzem":
        return True
    return False


def resolve_base_rate_position(property_type: str | None, facts: dict) -> float:
    """Pick conservative position in CSV range based on property type and view."""
    if is_penthouse_or_seafront(property_type, facts):
        return 0.80
    if facts.get("has_sea_view") or facts.get("micro_zone") in {"premium", "seafront"}:
        return 0.60
    ptype = _normalize_property_type(property_type)
    if any(token in ptype for token in _TOWNHOUSE_TYPES):
        return 0.45
    return 0.30


def resolve_base_rate(
    locality: str | None,
    property_type: str | None,
    facts: dict,
    *,
    csv_path: str | None = None,
) -> float | None:
    """Return base €/m² internal rate for a listing (before rate modifiers)."""
    rate_range = eur_per_sqm_range_for(locality, csv_path=csv_path)
    if rate_range is None:
        return None
    min_rate, max_rate = rate_range
    position = resolve_base_rate_position(property_type, facts)
    return interpolate_rate(min_rate, max_rate, position)


def _parse_coord(value: str | None) -> float | None:
    if value is None or not str(value).strip():
        return None
    try:
        return float(str(value).strip().replace(",", "."))
    except ValueError:
        return None


def _parse_km(value: str) -> float | None:
    match = re.search(r"([\d]+(?:[.,]\d+)?)", value.replace(",", "."))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _normalize_sea_proximity(value: str | None) -> SeaProximity | None:
    if not value or not str(value).strip():
        return None
    text = _normalize(str(value).replace("_", " "))
    if text in {"nad morzem", "seafront", "on sea", "at sea"}:
        return "nad_morzem"
    if text in {"blisko", "close", "near sea", "near the sea"}:
        return "blisko"
    if text in {"daleko", "far", "inland"}:
        return "daleko"
    return None


def _csv_key_variants(name: str) -> set[str]:
    """Build lookup keys from a CSV locality cell (incl. parenthetical aliases)."""
    keys: set[str] = set()
    raw = name.strip()
    if not raw:
        return keys
    keys.add(_normalize(raw))

    if "(" in raw and ")" in raw:
        before = raw.split("(", 1)[0].strip()
        inside = raw[raw.find("(") + 1 : raw.rfind(")")].strip()
        if before:
            keys.add(_normalize(before))
        if inside:
            keys.add(_normalize(inside))

    for key in list(keys):
        if key in _ALIASES:
            keys.add(_ALIASES[key])
    return {k for k in keys if k}


@lru_cache(maxsize=1)
def load_locality_profiles(csv_path: str | None = None) -> dict[str, LocalityProfile]:
    """Map normalized locality name → profile from to_gzira.csv."""
    path = Path(csv_path) if csv_path else GZIRA_CSV_PATH
    mapping: dict[str, LocalityProfile] = {}
    if not path.exists():
        print(f"⚠️ Brak pliku odległości: {path}")
        return mapping

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            locality = (row.get("Miejscowość") or row.get("locality") or "").strip()
            distance_raw = (
                row.get("Odległość do Gżiry")
                or row.get("Odleglosc do Gziry")
                or row.get("distance_km")
                or ""
            ).strip()
            sea_raw = (
                row.get("Dystans do morza")
                or row.get("Odleglosc do morza")
                or row.get("sea_proximity")
                or ""
            ).strip()
            region = (row.get("Region") or row.get("region") or "").strip() or None
            rate_raw = (
                row.get("Szacowana stawka €/m2")
                or row.get("Szacowana stawka EUR/m2")
                or row.get("eur_per_sqm")
                or ""
            ).strip()
            km = _parse_km(distance_raw)
            if not locality or km is None:
                continue
            rate_range = _parse_eur_per_sqm_range(rate_raw)
            profile: LocalityProfile = {
                "gzira_km": km,
                "sea_proximity": _normalize_sea_proximity(sea_raw),
                "region": region,
                "eur_per_sqm_min": rate_range[0] if rate_range else None,
                "eur_per_sqm_max": rate_range[1] if rate_range else None,
            }
            for key in _csv_key_variants(locality):
                mapping[key] = profile
    return mapping


@lru_cache(maxsize=1)
def load_gzira_distances(csv_path: str | None = None) -> dict[str, float]:
    """Map normalized locality name → distance km to Gżira."""
    return {key: profile["gzira_km"] for key, profile in load_locality_profiles(csv_path).items()}


@lru_cache(maxsize=1)
def load_locality_coords(csv_path: str | None = None) -> dict[str, LocalityCoords]:
    """Map normalized locality name → lat/lng from to_gzira.csv (incl. aliases)."""
    path = Path(csv_path) if csv_path else GZIRA_CSV_PATH
    mapping: dict[str, LocalityCoords] = {}
    if not path.exists():
        print(f"⚠️ Brak pliku odległości: {path}")
        return mapping

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            locality = (row.get("Miejscowość") or row.get("locality") or "").strip()
            lat = _parse_coord(
                row.get("Szerokość geograficzna")
                or row.get("Szerokosc geograficzna")
                or row.get("latitude")
                or row.get("lat")
            )
            lng = _parse_coord(
                row.get("Długość geograficzna")
                or row.get("Dlugosc geograficzna")
                or row.get("longitude")
                or row.get("lng")
            )
            if not locality or lat is None or lng is None:
                continue
            coords: LocalityCoords = {"name": locality, "lat": lat, "lng": lng}
            for key in _csv_key_variants(locality):
                mapping[key] = coords

    for alias, canonical in _ALIASES.items():
        if alias not in mapping and canonical in mapping:
            mapping[alias] = mapping[canonical]
    return mapping


def locality_coords_payload(csv_path: str | None = None) -> dict[str, object]:
    """JSON payload for the map tooltip: lookup table + projection bounds."""
    lookup = load_locality_coords(csv_path)
    return {"lookup": lookup, "bounds": dict(MAP_BOUNDS)}


def _candidate_tokens(locality: str) -> list[str]:
    """Generate match candidates from a free-form locality string."""
    text = locality.strip()
    if not text:
        return []

    parts: list[str] = [text]
    parts.extend(p.strip() for p in re.split(r"[,/|]", text) if p.strip())

    candidates: list[str] = []
    for part in parts:
        norm = _normalize(part)
        if not norm:
            continue
        candidates.append(norm)
        if norm in _ALIASES:
            candidates.append(_ALIASES[norm])
        for suffix in (" malta", " area", " bay"):
            if norm.endswith(suffix) and len(norm) > len(suffix) + 2:
                trimmed = norm[: -len(suffix)].strip()
                candidates.append(trimmed)
                if trimmed in _ALIASES:
                    candidates.append(_ALIASES[trimmed])
        if "(" in part:
            before = _normalize(part.split("(", 1)[0])
            if before:
                candidates.append(before)
                if before in _ALIASES:
                    candidates.append(_ALIASES[before])

    seen: set[str] = set()
    ordered: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def _lookup_profile(
    locality: str | None,
    csv_path: str | None = None,
) -> LocalityProfile | None:
    if not locality or not str(locality).strip():
        return None
    mapping = load_locality_profiles(csv_path)
    if not mapping:
        return None

    for candidate in _candidate_tokens(str(locality)):
        if candidate in mapping:
            return mapping[candidate]

    joined = _normalize(str(locality))
    for key, profile in mapping.items():
        if len(key) >= 4 and (key in joined or joined in key):
            return profile
    return None


def locality_profile_for(
    locality: str | None,
    csv_path: str | None = None,
) -> LocalityProfile | None:
    """Return full locality profile (Gżira km, sea proximity, region)."""
    return _lookup_profile(locality, csv_path)


def distance_to_gzira_km(locality: str | None, csv_path: str | None = None) -> float | None:
    """Return estimated km to Gżira for a locality, or None if unknown."""
    profile = _lookup_profile(locality, csv_path)
    return profile["gzira_km"] if profile else None


def sea_proximity_for(
    locality: str | None,
    csv_path: str | None = None,
) -> SeaProximity | None:
    """Return sea proximity category for a locality, or None if unknown."""
    profile = _lookup_profile(locality, csv_path)
    return profile["sea_proximity"] if profile else None


def eur_per_sqm_range_for(
    locality: str | None,
    csv_path: str | None = None,
) -> tuple[float, float] | None:
    """Return (min, max) €/m² for a locality from to_gzira.csv."""
    profile = _lookup_profile(locality, csv_path)
    if not profile:
        return None
    min_rate = profile.get("eur_per_sqm_min")
    max_rate = profile.get("eur_per_sqm_max")
    if min_rate is None or max_rate is None:
        return None
    return (min_rate, max_rate)


def locality_rate_tier(locality: str | None, csv_path: str | None = None) -> str:
    """Classify locality as high/mid/entry tier from CSV range width and level."""
    rate_range = eur_per_sqm_range_for(locality, csv_path=csv_path)
    if rate_range is None:
        return "mid"
    min_rate, max_rate = rate_range
    mid = (min_rate + max_rate) / 2
    if mid >= 4000:
        return "high"
    if mid >= 2800:
        return "mid"
    return "entry"
