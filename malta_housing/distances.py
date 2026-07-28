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

SeaProximity = Literal["nad_morzem", "blisko", "daleko"]


class LocalityProfile(TypedDict):
    gzira_km: float
    sea_proximity: SeaProximity | None
    region: str | None


# Common scraper/LLM locality spellings → CSV town keys (normalized).
_ALIASES: dict[str, str] = {
    "birzebbugia": "birzebbuga",
    "birzebbuga": "birzebbuga",
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
    "qajjenza": "birzebbuga",
    "zebbiegh": "mgarr",
    "ta xbiex": "ta xbiex",
    "santa lucia": "santa lucija",
    "rabat malta": "rabat",
}


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
            km = _parse_km(distance_raw)
            if not locality or km is None:
                continue
            profile: LocalityProfile = {
                "gzira_km": km,
                "sea_proximity": _normalize_sea_proximity(sea_raw),
                "region": region,
            }
            for key in _csv_key_variants(locality):
                mapping[key] = profile
    return mapping


@lru_cache(maxsize=1)
def load_gzira_distances(csv_path: str | None = None) -> dict[str, float]:
    """Map normalized locality name → distance km to Gżira."""
    return {key: profile["gzira_km"] for key, profile in load_locality_profiles(csv_path).items()}


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
