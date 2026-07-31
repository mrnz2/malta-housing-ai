"""Bank-style property valuation engine (deterministic EUR estimates)."""

from __future__ import annotations

from typing import Any

from malta_housing.distances import (
    interpolate_rate,
    locality_rate_tier,
    resolve_base_rate,
    resolve_base_rate_position,
    eur_per_sqm_range_for,
)

SANITY_RATIO_LOW = 0.60
SANITY_RATIO_HIGH = 1.50

TOTAL_TO_INTERNAL_RATIO = 0.85

FINISH_RATE_FACTORS: dict[str, float] = {
    "shell": 0.80,
    "finished": 1.0,
    "furnished": 1.07,
    "needs_renovation": 0.90,
}

GARAGE_PREMIUM_BY_TIER: dict[str, tuple[int, int]] = {
    "high": (35_000, 45_000),
    "mid": (30_000, 38_000),
    "entry": (25_000, 32_000),
}

URGENT_RENOVATION_DEDUCTION_EUR = 15_000


def _round_eur(value: float) -> int:
    return int(round(value, 0))


def _default_garage_facts() -> dict[str, Any]:
    return {
        "included_in_price": False,
        "optional_extra_price": False,
        "garage_price_eur": None,
    }


def _default_legal_facts() -> dict[str, Any]:
    return {
        "is_freehold": True,
        "has_ground_rent": False,
        "ground_rent_type": None,
        "ground_rent_annual_eur": None,
        "ground_rent_years_remaining": None,
        "has_emphyteusis": False,
    }


def normalize_valuation_facts(
    raw: dict[str, Any] | None,
    *,
    listing_flags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge LLM/regex facts into a consistent valuation_facts dict."""
    listing_flags = listing_flags or {}
    data = dict(raw or {})

    garage_raw = data.get("garage")
    garage = _default_garage_facts()
    if isinstance(garage_raw, dict):
        garage.update({k: v for k, v in garage_raw.items() if v is not None})

    legal_raw = data.get("legal")
    legal = _default_legal_facts()
    if isinstance(legal_raw, dict):
        legal.update({k: v for k, v in legal_raw.items() if v is not None})
    if listing_flags.get("is_freehold"):
        legal["is_freehold"] = True

    facts: dict[str, Any] = {
        "internal_area_sqm": data.get("internal_area_sqm"),
        "external_area_sqm": data.get("external_area_sqm"),
        "total_area_sqm": data.get("total_area_sqm"),
        "floor_level": data.get("floor_level"),
        "has_lift": data.get("has_lift"),
        "finish_level": data.get("finish_level"),
        "micro_zone": data.get("micro_zone") or "standard",
        "condition_issues": list(data.get("condition_issues") or []),
        "garage": garage,
        "legal": legal,
        "has_sea_view": bool(data.get("has_sea_view", listing_flags.get("has_sea_view"))),
        "has_airspace": bool(data.get("has_airspace", listing_flags.get("has_airspace"))),
        "is_shell_form": bool(data.get("is_shell_form", listing_flags.get("is_shell_form"))),
        "sea_proximity": data.get("sea_proximity", listing_flags.get("sea_proximity")),
    }
    return facts


def apply_area_heuristics(facts: dict[str, Any]) -> list[str]:
    """Fill missing internal from total; return sanity flag codes."""
    flags: list[str] = []
    internal = facts.get("internal_area_sqm")
    total = facts.get("total_area_sqm")

    if internal is None and total is not None:
        try:
            total_val = float(total)
            if 10 <= total_val <= 10_000:
                facts["internal_area_sqm"] = int(round(total_val * TOTAL_TO_INTERNAL_RATIO))
                flags.append("area_heuristic")
        except (TypeError, ValueError):
            pass

    if facts.get("internal_area_sqm") is None:
        flags.append("missing_internal_area")

    return flags


def _resolve_finish_level(facts: dict[str, Any], listing_flags: dict[str, Any]) -> str:
    finish = str(facts.get("finish_level") or "").strip().lower()
    if finish in FINISH_RATE_FACTORS:
        return finish
    if listing_flags.get("is_shell_form"):
        return "shell"
    ready = listing_flags.get("ready")
    if ready is False:
        return "needs_renovation"
    return "finished"


def _finish_rate_factor(finish_level: str) -> float:
    return FINISH_RATE_FACTORS.get(finish_level, 1.0)


def _lift_rate_factor(facts: dict[str, Any]) -> float:
    floor = facts.get("floor_level")
    has_lift = facts.get("has_lift")
    if has_lift is True:
        return 1.0
    if has_lift is False and floor is not None:
        try:
            if int(floor) > 2:
                return 0.90
        except (TypeError, ValueError):
            pass
    return 1.0


def _condition_rate_factor(condition_issues: list[str]) -> float:
    if not condition_issues:
        return 1.0
    joined = " ".join(str(x).lower() for x in condition_issues)
    if any(token in joined for token in ("damp", "mould", "mold", "structural", "asbestos")):
        return 0.85
    if any(token in joined for token in ("old", "dated", "wear", "repair", "renovation")):
        return 0.90
    return 0.95


def _airspace_rate_factor(facts: dict[str, Any]) -> float:
    if facts.get("has_airspace"):
        return 1.15
    return 1.0


def _ground_rent_rate_factor(legal: dict[str, Any]) -> float:
    if legal.get("has_emphyteusis"):
        return 0.85
    rent_type = str(legal.get("ground_rent_type") or "").lower()
    if legal.get("has_ground_rent"):
        if rent_type == "temporary":
            return 1.0
        return 0.97
    return 1.0


def _external_weight(facts: dict[str, Any]) -> float:
    if facts.get("has_sea_view") or facts.get("micro_zone") in {"seafront", "premium"}:
        return 0.50
    if facts.get("micro_zone") == "quiet":
        return 0.30
    return 0.40


def _garage_fixed_eur(locality: str | None, garage: dict[str, Any]) -> int:
    if garage.get("optional_extra_price"):
        return 0
    if not garage.get("included_in_price"):
        return 0
    tier = locality_rate_tier(locality)
    low, high = GARAGE_PREMIUM_BY_TIER.get(tier, GARAGE_PREMIUM_BY_TIER["mid"])
    return (low + high) // 2


def _temporary_ground_rent_eur(subtotal_sqm: float, legal: dict[str, Any]) -> int:
    rent_type = str(legal.get("ground_rent_type") or "").lower()
    if not legal.get("has_ground_rent") or rent_type != "temporary":
        return 0
    years = legal.get("ground_rent_years_remaining")
    if years is not None:
        try:
            years_val = int(years)
            if years_val <= 20:
                return _round_eur(subtotal_sqm * 0.40)
            if years_val <= 40:
                return _round_eur(subtotal_sqm * 0.25)
        except (TypeError, ValueError):
            pass
    return _round_eur(subtotal_sqm * 0.30)


def _renovation_deduction_eur(condition_issues: list[str]) -> int:
    joined = " ".join(str(x).lower() for x in condition_issues)
    if any(token in joined for token in ("urgent", "major", "structural", "roof", "damp")):
        return URGENT_RENOVATION_DEDUCTION_EUR
    return 0


def _bank_risk_label(
    gap_pct: float,
    *,
    confidence: str,
    legal: dict[str, Any],
    price_ratio: float,
) -> str:
    rent_type = str(legal.get("ground_rent_type") or "").lower()
    if legal.get("has_ground_rent") and rent_type == "temporary":
        return "high"
    if price_ratio > SANITY_RATIO_HIGH or gap_pct > 15:
        return "high"
    if confidence == "low" or gap_pct > 5:
        return "medium"
    return "low"


def compute_bank_valuation(
    listing: dict[str, Any],
    facts: dict[str, Any],
) -> dict[str, Any]:
    """Compute bank-style EUR valuation and risk metadata."""
    locality = listing.get("locality")
    property_type = listing.get("property_type")
    asking_price = listing.get("price_eur")

    listing_flags = {
        "has_sea_view": listing.get("has_sea_view"),
        "has_airspace": listing.get("has_airspace"),
        "is_shell_form": listing.get("is_shell_form"),
        "ready": listing.get("ready"),
        "is_freehold": listing.get("is_freehold"),
        "sea_proximity": listing.get("sea_proximity"),
    }

    normalized = normalize_valuation_facts(facts, listing_flags=listing_flags)
    heuristic_flags = apply_area_heuristics(normalized)
    legal = normalized["legal"]
    garage = normalized["garage"]

    rate_range = eur_per_sqm_range_for(locality)
    base_rate = resolve_base_rate(locality, property_type, normalized)

    missing_inputs: list[str] = []
    if rate_range is None or base_rate is None:
        missing_inputs.append("locality_rate")
    internal_sqm = normalized.get("internal_area_sqm")
    if internal_sqm is None:
        missing_inputs.append("internal_area_sqm")

    finish_level = _resolve_finish_level(normalized, listing_flags)
    rate_modifiers = {
        "finish": _finish_rate_factor(finish_level),
        "lift": _lift_rate_factor(normalized),
        "condition": _condition_rate_factor(normalized.get("condition_issues", [])),
        "airspace": _airspace_rate_factor(normalized),
        "ground_rent": _ground_rent_rate_factor(legal),
    }
    adjusted_rate = base_rate or 0.0
    for factor in rate_modifiers.values():
        adjusted_rate *= factor

    internal_value = 0.0
    external_value = 0.0
    if internal_sqm is not None and adjusted_rate:
        try:
            internal_value = float(internal_sqm) * adjusted_rate
        except (TypeError, ValueError):
            pass
    external_sqm = normalized.get("external_area_sqm")
    if external_sqm is not None and adjusted_rate:
        try:
            external_value = float(external_sqm) * adjusted_rate * _external_weight(normalized)
        except (TypeError, ValueError):
            pass

    subtotal_sqm = internal_value + external_value
    garage_fixed = _garage_fixed_eur(locality, garage)
    temp_rent_eur = _temporary_ground_rent_eur(subtotal_sqm, legal)
    renovation_eur = _renovation_deduction_eur(normalized.get("condition_issues", []))

    estimated_value = max(0.0, subtotal_sqm + garage_fixed - temp_rent_eur - renovation_eur)

    range_low = None
    range_high = None
    if rate_range and internal_sqm is not None:
        min_rate, max_rate = rate_range
        low_pos = resolve_base_rate_position(property_type, {**normalized, "has_sea_view": False, "micro_zone": "standard"})
        high_pos = 0.85
        low_rate = interpolate_rate(min_rate, max_rate, low_pos)
        high_rate = interpolate_rate(min_rate, max_rate, high_pos)
        for factor in rate_modifiers.values():
            low_rate *= factor
            high_rate *= factor
        try:
            internal_f = float(internal_sqm)
            ext_f = float(external_sqm or 0)
            ext_w = _external_weight(normalized)
            range_low = max(
                0.0,
                internal_f * low_rate + ext_f * low_rate * ext_w + garage_fixed - temp_rent_eur - renovation_eur,
            )
            range_high = max(
                0.0,
                internal_f * high_rate + ext_f * high_rate * ext_w + garage_fixed - temp_rent_eur - renovation_eur,
            )
        except (TypeError, ValueError):
            pass

    gap_eur: int | None = None
    gap_pct: float | None = None
    price_ratio: float | None = None
    if asking_price is not None and estimated_value > 0:
        gap_eur = int(asking_price) - _round_eur(estimated_value)
        gap_pct = round((gap_eur / estimated_value) * 100, 1)
        price_ratio = round(asking_price / estimated_value, 3)

    confidence = "medium"
    sanity_flags = list(heuristic_flags)
    if price_ratio is not None and (
        price_ratio < SANITY_RATIO_LOW or price_ratio > SANITY_RATIO_HIGH
    ):
        confidence = "low"
        sanity_flags.append("price_area_mismatch")
    if "internal_area_sqm" in missing_inputs or internal_sqm is None:
        confidence = "low"
    if garage.get("optional_extra_price"):
        sanity_flags.append("optional_garage_excluded")

    bank_risk = _bank_risk_label(
        gap_pct or 0.0,
        confidence=confidence,
        legal=legal,
        price_ratio=price_ratio or 1.0,
    )

    return {
        "estimated_value_eur": _round_eur(estimated_value) if estimated_value else None,
        "estimated_range_low_eur": _round_eur(range_low) if range_low is not None else None,
        "estimated_range_high_eur": _round_eur(range_high) if range_high is not None else None,
        "asking_price_eur": asking_price,
        "gap_eur": gap_eur,
        "gap_pct": gap_pct,
        "price_ratio": price_ratio,
        "bank_risk": bank_risk,
        "confidence": confidence,
        "sanity_flags": sanity_flags,
        "components": {
            "base_rate_eur_per_sqm": round(base_rate, 2) if base_rate else None,
            "adjusted_rate_eur_per_sqm": round(adjusted_rate, 2) if adjusted_rate else None,
            "internal_value": _round_eur(internal_value),
            "external_value": _round_eur(external_value),
            "subtotal_sqm": _round_eur(subtotal_sqm),
            "garage_fixed_eur": garage_fixed,
            "ground_rent_adjustment_eur": temp_rent_eur,
            "renovation_deduction_eur": renovation_eur,
        },
        "rate_modifiers": rate_modifiers,
        "finish_level": finish_level,
        "missing_inputs": missing_inputs,
    }
