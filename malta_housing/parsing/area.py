"""Extract advertised floor area (m²) from listing text."""

from __future__ import annotations

import re

# Decimal areas (130.50) and units incl. Frank Salt "sq mt", re316 "m 2".
_AREA_NUM = r"(\d+(?:[.,]\d+)?)"
_AREA_UNIT = (
    r"(?:sq\.?\s*m(?:t|eters?|etres?)?|sqm|m\s*[²2]|m²|square\s*met(?:er|re)s?)"
)

_INTERNAL_AREA_PATTERNS = (
    re.compile(
        rf"internal\s+area(?:\s*m\s*[2²])?[:\s]*{_AREA_NUM}(?:\s*{_AREA_UNIT})?",
        re.I,
    ),
    re.compile(rf"TotalIntArea[:\s\"]*{_AREA_NUM}", re.I),
)
_EXTERNAL_AREA_PATTERNS = (
    re.compile(
        rf"external\s+area(?:\s*m\s*[2²])?[:\s]*{_AREA_NUM}(?:\s*{_AREA_UNIT})?",
        re.I,
    ),
    re.compile(rf"TotalExtArea[:\s\"]*{_AREA_NUM}", re.I),
)
_TOTAL_AREA_PATTERNS = (
    re.compile(
        rf"(?:total|gross)\s+area\s*\(\s*m\s*[2²]\s*\)\s*:?\s*{_AREA_NUM}",
        re.I,
    ),
    re.compile(
        rf"(?:total|gross)\s+area(?:\s*m\s*[2²])?[:\s]*{_AREA_NUM}(?:\s*{_AREA_UNIT})?",
        re.I,
    ),
    re.compile(
        # Bare "Area m2: 95" (RE/MAX, Yitaku, Belair) — not internal/external.
        rf"(?<![Ii]nternal )(?<![Ee]xternal )area\s*m\s*[2²]\s*[:\s]*{_AREA_NUM}",
        re.I,
    ),
    re.compile(rf"{_AREA_NUM}\s*{_AREA_UNIT}\s*(?:total|gross)", re.I),
)
_GENERIC_AREA_PATTERN = re.compile(rf"{_AREA_NUM}\s*{_AREA_UNIT}", re.I)
# Skip UI filter junk like "0 - 100 Sqm".
_AREA_RANGE_PREFIX = re.compile(r"\d+(?:[.,]\d+)?\s*[\-–—]\s*$")


def valid_area(value: int, *, minimum: int = 10) -> bool:
    return minimum <= value <= 10_000


def _parse_area_number(raw: str) -> int | None:
    try:
        value = float(str(raw).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return int(round(value))


def _first_area(
    patterns: tuple[re.Pattern[str], ...],
    text: str,
    *,
    minimum: int = 10,
) -> int | None:
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        value = _parse_area_number(match.group(1))
        if value is not None and valid_area(value, minimum=minimum):
            return value
    return None


def extract_areas_from_text(text: str) -> dict[str, int | None]:
    internal = _first_area(_INTERNAL_AREA_PATTERNS, text)
    external = _first_area(_EXTERNAL_AREA_PATTERNS, text, minimum=1)
    total = _first_area(_TOTAL_AREA_PATTERNS, text)
    if internal is None and total is None:
        for match in _GENERIC_AREA_PATTERN.finditer(text):
            prefix = text[max(0, match.start(1) - 24) : match.start(1)]
            if _AREA_RANGE_PREFIX.search(prefix):
                continue
            value = _parse_area_number(match.group(1))
            if value is not None and valid_area(value):
                total = value
                break
    return {
        "internal_area_sqm": internal,
        "external_area_sqm": external,
        "total_area_sqm": total,
    }


def resolve_area_sqm(
    internal: int | None = None, total: int | None = None
) -> int | None:
    """Prefer internal (living) area; fall back to advertised total."""
    if internal is not None:
        return int(internal)
    if total is not None:
        return int(total)
    return None


def area_sqm_from_text(text: str | None) -> int | None:
    if not text or not str(text).strip():
        return None
    areas = extract_areas_from_text(str(text))
    return resolve_area_sqm(areas.get("internal_area_sqm"), areas.get("total_area_sqm"))


_AREA_FEATURE_INTERNAL = re.compile(
    r"(?:internal|wewn(?:ętrzn)?(?:a|e)?)\s*(?:area|powierzchnia)?|"
    r"(?:area|powierzchnia)\s*(?:internal|wewn(?:ętrzn)?(?:a|e)?)",
    re.I,
)
_AREA_FEATURE_TOTAL = re.compile(
    r"(?:total|gross|netto|całkowit(?:a|e)?|cała)\s*(?:area|powierzchnia)?|"
    r"(?:area|powierzchnia)\s*(?:total|gross|netto|całkowit(?:a|e)?|cała)",
    re.I,
)
_AREA_FEATURE_NUM_BEFORE = re.compile(
    rf"{_AREA_NUM}\s*(?:{_AREA_UNIT}|m\s*[²2])",
    re.I,
)
_AREA_FEATURE_NUM_AFTER = re.compile(
    rf"(?:powierzchnia|area|metra(?:ż|z)|size)[^\d]{{0,24}}{_AREA_NUM}\s*(?:{_AREA_UNIT}|m\s*[²2])?",
    re.I,
)
_AREA_FEATURE_NUM_AFTER_UNIT_FIRST = re.compile(
    r"(?:powierzchnia|area|metra(?:ż|z)|size)[^\d]{0,24}" + _AREA_NUM,
    re.I,
)


def _area_from_feature_phrase(text: str) -> tuple[int, str] | None:
    """Return (sqm, kind) from a short feature/title phrase. kind: internal|total|generic."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return None

    kind = "generic"
    if _AREA_FEATURE_INTERNAL.search(cleaned):
        kind = "internal"
    elif _AREA_FEATURE_TOTAL.search(cleaned):
        kind = "total"

    for pattern in (
        _AREA_FEATURE_NUM_BEFORE,
        _AREA_FEATURE_NUM_AFTER,
        _AREA_FEATURE_NUM_AFTER_UNIT_FIRST,
    ):
        match = pattern.search(cleaned)
        if not match:
            continue
        value = _parse_area_number(match.group(1))
        if value is not None and valid_area(value):
            return value, kind

    fallback = area_sqm_from_text(cleaned)
    if fallback is not None:
        return fallback, kind
    return None


def best_area_from_strings(strings: list[str] | tuple[str, ...]) -> int | None:
    """Pick best m² from UI feature/title strings (internal > total > generic)."""
    internal: int | None = None
    total: int | None = None
    generic: int | None = None
    for raw in strings:
        parsed = _area_from_feature_phrase(raw)
        if parsed is None:
            continue
        value, kind = parsed
        if kind == "internal":
            internal = value
        elif kind == "total" and total is None:
            total = value
        elif kind == "generic" and generic is None:
            generic = value
    return resolve_area_sqm(internal, total) or generic
