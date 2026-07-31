"""Canonical property type codes and normalization from legacy free-form values."""

from __future__ import annotations

import re

PropertyTypeCode = str

CANONICAL_PROPERTY_TYPES: frozenset[str] = frozenset(
    {
        "apartment",
        "maisonette",
        "penthouse",
        "garage",
        "town_house",
        "terraced_house",
        "house_of_character",
        "studio",
        "land",
        "villa",
        "duplex",
        "business",
        "other",
    }
)

_ALIASES: list[tuple[re.Pattern[str], PropertyTypeCode]] = [
    (re.compile(r"apartment|flat|apt", re.I), "apartment"),
    (re.compile(r"maisonette", re.I), "maisonette"),
    (re.compile(r"penthouse", re.I), "penthouse"),
    (re.compile(r"garage|car\s*space|parking", re.I), "garage"),
    (re.compile(r"town\s*house", re.I), "town_house"),
    (re.compile(r"terraced", re.I), "terraced_house"),
    (re.compile(r"house\s+of\s+character|character\s+house", re.I), "house_of_character"),
    (re.compile(r"studio", re.I), "studio"),
    (re.compile(r"land|plot|odz", re.I), "land"),
    (re.compile(r"villa|detached", re.I), "villa"),
    (re.compile(r"duplex", re.I), "duplex"),
    (re.compile(r"shop|business|outlet|commercial|office", re.I), "business"),
]


def normalize_property_type(value: str | None) -> PropertyTypeCode | None:
    """Map free-form LLM/portal text to a canonical property type code."""
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    lowered = text.lower().replace(" ", "_").replace("/", "_")
    if lowered in CANONICAL_PROPERTY_TYPES:
        return lowered
    for pattern, code in _ALIASES:
        if pattern.search(text):
            return code
    return "other"
