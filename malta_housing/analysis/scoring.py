"""Deterministic base score for investment ranking (0–8 points), bank-valuation aligned."""

from __future__ import annotations

from typing import Any

from malta_housing.distances import SeaProximity

MAX_VALUE_VS_MARKET = 3.5
MAX_VALUE_VS_MARKET_LOW_CONF = 1.5
MAX_LOCATION = 1.5
MAX_PROPERTY_FIT = 1.5
MAX_FINISH_AMENITIES = 1.0
MAX_LEGAL_SAFETY = 0.5

AREA_FLOOR_SQM = 50
AREA_FULL_SCORE_SQM = 150
AREA_MIN_SCORE = 0.2

SEA_SCORES: dict[SeaProximity, float] = {
    "nad_morzem": 0.6,
    "blisko": 0.35,
    "daleko": 0.1,
}
MICRO_ZONE_BONUS: dict[str, float] = {
    "seafront": 0.4,
    "premium": 0.25,
    "standard": 0.0,
    "quiet": -0.1,
}


def _round_score(value: float) -> float:
    return round(value, 2)


def _score_value_vs_market(bank_valuation: dict[str, Any]) -> float:
    confidence = bank_valuation.get("confidence", "medium")
    gap_pct = bank_valuation.get("gap_pct")
    cap = MAX_VALUE_VS_MARKET_LOW_CONF if confidence == "low" else MAX_VALUE_VS_MARKET

    if gap_pct is None:
        return _round_score(min(cap, 1.0))

    gap = float(gap_pct)
    if gap <= -10:
        score = cap
    elif gap <= 0:
        score = cap * 0.85
    elif gap <= 5:
        score = cap * 0.65
    elif gap <= 10:
        score = cap * 0.40
    elif gap <= 15:
        score = cap * 0.15
    else:
        score = 0.0
    return _round_score(min(cap, score))


def _score_location_quality(
    metrics: dict[str, Any],
    facts: dict[str, Any],
    bank_valuation: dict[str, Any],
) -> float:
    score = 0.0
    sea = metrics.get("sea_proximity")
    if sea in SEA_SCORES:
        score += SEA_SCORES[sea]  # type: ignore[index]

    micro = str(facts.get("micro_zone") or "standard").lower()
    score += MICRO_ZONE_BONUS.get(micro, 0.0)

    base_rate = bank_valuation.get("components", {}).get("base_rate_eur_per_sqm")
    if base_rate is not None:
        if base_rate >= 4500:
            score += 0.4
        elif base_rate >= 3200:
            score += 0.25
        else:
            score += 0.1

    return _round_score(max(0.0, min(MAX_LOCATION, score)))


def _score_property_fit(metrics: dict[str, Any], facts: dict[str, Any]) -> float:
    score = 0.0
    internal = facts.get("internal_area_sqm") or metrics.get("area_sqm")
    if internal is None:
        score += 0.4
    else:
        try:
            area = float(internal)
            if area <= AREA_FLOOR_SQM:
                score += AREA_MIN_SCORE
            elif area >= AREA_FULL_SCORE_SQM:
                score += 0.6
            else:
                ratio = (area - AREA_FLOOR_SQM) / (AREA_FULL_SCORE_SQM - AREA_FLOOR_SQM)
                score += AREA_MIN_SCORE + (0.6 - AREA_MIN_SCORE) * ratio
        except (TypeError, ValueError):
            score += 0.4

    bedrooms = metrics.get("bedrooms")
    if bedrooms is not None and internal is not None:
        try:
            area = float(internal)
            beds = int(bedrooms)
            sqm_per_bed = area / max(beds, 1)
            if 25 <= sqm_per_bed <= 55:
                score += 0.5
            elif sqm_per_bed < 25:
                score += 0.25
            else:
                score += 0.35
        except (TypeError, ValueError):
            score += 0.3
    else:
        score += 0.3

    prop_type = str(metrics.get("property_type") or "").lower()
    if any(token in prop_type for token in ("apartment", "maisonette", "penthouse")):
        score += 0.4
    elif prop_type:
        score += 0.3

    return _round_score(max(0.0, min(MAX_PROPERTY_FIT, score)))


def _score_finish_amenities(facts: dict[str, Any], metrics: dict[str, Any]) -> float:
    score = 0.0
    finish = str(facts.get("finish_level") or bank_valuation_finish(metrics, facts)).lower()
    if finish == "furnished":
        score += 0.3
    elif finish == "finished":
        score += 0.25
    elif finish == "needs_renovation":
        score += 0.1
    elif finish == "shell":
        score += 0.0

    floor = facts.get("floor_level")
    has_lift = facts.get("has_lift")
    if has_lift is True:
        score += 0.2
    elif has_lift is False and floor is not None:
        try:
            if int(floor) > 2:
                score += 0.0
            else:
                score += 0.15
        except (TypeError, ValueError):
            score += 0.1
    else:
        score += 0.1

    garage = facts.get("garage") or {}
    if isinstance(garage, dict) and garage.get("included_in_price"):
        score += 0.25
    if facts.get("has_airspace") or metrics.get("has_airspace"):
        score += 0.2

    return _round_score(max(0.0, min(MAX_FINISH_AMENITIES, score)))


def bank_valuation_finish(metrics: dict[str, Any], facts: dict[str, Any]) -> str:
    finish = str(facts.get("finish_level") or "").strip().lower()
    if finish:
        return finish
    if metrics.get("is_shell_form"):
        return "shell"
    if metrics.get("ready") is False:
        return "needs_renovation"
    return "finished"


def _score_legal_safety(metrics: dict[str, Any], facts: dict[str, Any]) -> float:
    score = 0.0
    legal = facts.get("legal") or {}
    if metrics.get("is_freehold") or legal.get("is_freehold"):
        score += 0.25
    rent_type = str(legal.get("ground_rent_type") or "").lower()
    if legal.get("has_ground_rent"):
        if rent_type == "temporary":
            return 0.0
        score += 0.1
    if legal.get("has_emphyteusis"):
        return 0.0
    return _round_score(max(0.0, min(MAX_LEGAL_SAFETY, score)))


def compute_base_score(
    metrics: dict[str, Any],
    bank_valuation: dict[str, Any],
    facts: dict[str, Any],
) -> dict[str, Any]:
    """Return base_score (0–8) and per-component breakdown."""
    components = {
        "value_vs_market": _score_value_vs_market(bank_valuation),
        "location_quality": _score_location_quality(metrics, facts, bank_valuation),
        "property_fit": _score_property_fit(metrics, facts),
        "finish_amenities": _score_finish_amenities(facts, metrics),
        "legal_safety": _score_legal_safety(metrics, facts),
    }
    base_score = _round_score(sum(components.values()))
    return {
        "base_score": base_score,
        "components": components,
    }


if __name__ == "__main__":
    sample_valuation = {
        "gap_pct": -5.0,
        "confidence": "medium",
        "components": {"base_rate_eur_per_sqm": 5460},
    }
    sample_facts = {
        "internal_area_sqm": 95,
        "micro_zone": "standard",
        "finish_level": "finished",
        "has_lift": False,
        "floor_level": 3,
        "garage": {"included_in_price": False},
        "legal": {"is_freehold": True},
    }
    sample_metrics = {
        "sea_proximity": "nad_morzem",
        "property_type": "Apartment",
        "bedrooms": 2,
        "has_airspace": False,
        "is_shell_form": False,
        "ready": True,
        "is_freehold": True,
    }
    result = compute_base_score(sample_metrics, sample_valuation, sample_facts)
    print(f"Sample: base={result['base_score']} breakdown={result['components']}")
