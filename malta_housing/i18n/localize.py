"""Resolve bilingual DB columns and localize fixed codes for API responses."""

from __future__ import annotations

import json
from typing import Any

from malta_housing.i18n.messages import (
    label,
    localize_bank_valuation,
    localize_score_breakdown,
    normalize_locale,
)
from malta_housing.i18n.property_types import normalize_property_type

_BILINGUAL_FIELDS = (
    "title",
    "key_features",
    "ai_summary",
    "pros",
    "cons",
    "buyer_warnings",
)

_JSON_LIST_FIELDS = frozenset({"key_features", "pros", "cons", "buyer_warnings"})


def pick_locale_text(
    row: dict[str, Any],
    field: str,
    locale: str | None,
    *,
    fallback_en: bool = True,
) -> Any:
    """Pick field_en or field_pl with legacy single-column fallback."""
    loc = normalize_locale(locale)
    primary = row.get(f"{field}_{loc}")
    if primary is not None and primary != "" and primary != []:
        return primary
    if loc != "en":
        en_val = row.get(f"{field}_en")
        if en_val is not None and en_val != "" and en_val != []:
            return en_val
    if fallback_en:
        legacy = row.get(field)
        if legacy is not None and legacy != "" and legacy != []:
            return legacy
    return None


def _coerce_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if x is not None and str(x).strip()]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if x is not None and str(x).strip()]
        except json.JSONDecodeError:
            pass
    return []


def localize_listing(row: dict[str, Any], locale: str | None = "en") -> dict[str, Any]:
    """Return a copy with unified locale-specific text fields and code labels."""
    loc = normalize_locale(locale)
    out = dict(row)

    for field in _BILINGUAL_FIELDS:
        if field == "buyer_warnings":
            raw = pick_locale_text(row, "buyer_warnings", loc)
            if raw is None:
                raw = row.get("buyer_warnings_pl")
            out[field] = _coerce_json_list(raw)
        else:
            value = pick_locale_text(row, field, loc)
            if field in _JSON_LIST_FIELDS:
                out[field] = _coerce_json_list(value)
            else:
                out[field] = value

    prop_code = row.get("property_type")
    if prop_code:
        normalized = normalize_property_type(str(prop_code))
        out["property_type"] = normalized or prop_code
        out["property_type_label"] = label("property_type", normalized or prop_code, loc)
    else:
        out["property_type_label"] = "—"

    out["seller_type_label"] = label("seller_type", row.get("seller_type"), loc)
    out["source_label"] = label("source", row.get("source"), loc)
    out["sea_proximity_label"] = label("sea_proximity", row.get("sea_proximity"), loc)

    if "bank_valuation" in out:
        out["bank_valuation"] = localize_bank_valuation(out.get("bank_valuation"), loc)

    if "score_breakdown" in out:
        localized = localize_score_breakdown(out.get("score_breakdown"), loc)
        if localized is not None:
            out["score_breakdown"] = localized

    return out


def localized_filter_options(
    codes: list[str],
    category: str,
    locale: str | None,
) -> list[dict[str, str]]:
    loc = normalize_locale(locale)
    seen: set[str] = set()
    items: list[dict[str, str]] = []
    for code in codes:
        if not code or code in seen:
            continue
        seen.add(code)
        if category == "property_type":
            normalized = normalize_property_type(code) or code
            items.append(
                {
                    "code": normalized,
                    "label": label("property_type", normalized, loc),
                }
            )
        else:
            items.append({"code": code, "label": label(category, code, loc)})
    items.sort(key=lambda x: x["label"].lower())
    return items
