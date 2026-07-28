"""Deterministic base score for investment ranking (0–8 points)."""

from __future__ import annotations

from typing import Any

from malta_housing.distances import SeaProximity

# Price per m² tiers (EUR)
PRICE_PER_SQM_EXCELLENT = 2800
PRICE_PER_SQM_GOOD = 3500
PRICE_PER_SQM_FAIR = 4200

# Component caps
MAX_PRICE_PER_SQM = 2.5
MAX_GZIRA = 2.0
MAX_SEA = 1.5
MAX_AREA = 1.0
MAX_FLAGS = 1.0

GZIRA_FULL_SCORE_KM = 12.0

SEA_SCORES: dict[SeaProximity, float] = {
    "nad_morzem": 1.5,
    "blisko": 0.75,
    "daleko": 0.0,
}
SEA_VIEW_BONUS = 0.25

AREA_SWEET_MIN = 70
AREA_SWEET_MAX = 130
AREA_OK_MIN = 50
AREA_OK_MAX = 160


def _round_score(value: float) -> float:
    return round(value, 2)


def _score_price_per_sqm(price_per_sqm: float | None) -> float:
    if price_per_sqm is None:
        return 0.5
    if price_per_sqm <= PRICE_PER_SQM_EXCELLENT:
        return MAX_PRICE_PER_SQM
    if price_per_sqm <= PRICE_PER_SQM_GOOD:
        return 2.0
    if price_per_sqm <= PRICE_PER_SQM_FAIR:
        return 1.0
    return 0.0


def _score_gzira(distance_km: float | None) -> float:
    if distance_km is None:
        return 0.0
    if distance_km >= GZIRA_FULL_SCORE_KM:
        return 0.0
    return _round_score(MAX_GZIRA * max(0.0, 1.0 - distance_km / GZIRA_FULL_SCORE_KM))


def _score_sea(
    sea_proximity: SeaProximity | str | None,
    *,
    has_sea_view: bool,
) -> float:
    score = 0.0
    if sea_proximity in SEA_SCORES:
        score = SEA_SCORES[sea_proximity]  # type: ignore[index]
    if has_sea_view:
        score = min(MAX_SEA, score + SEA_VIEW_BONUS)
    return _round_score(score)


def _score_area(area_sqm: int | float | None) -> float:
    if area_sqm is None:
        return 0.3
    area = float(area_sqm)
    if AREA_SWEET_MIN <= area <= AREA_SWEET_MAX:
        return MAX_AREA
    if AREA_OK_MIN <= area < AREA_SWEET_MIN or AREA_SWEET_MAX < area <= AREA_OK_MAX:
        return 0.6
    return 0.2


def _score_flags(metrics: dict[str, Any]) -> float:
    score = 0.0
    if metrics.get("is_freehold"):
        score += 0.4
    if metrics.get("has_airspace"):
        score += 0.2
    if metrics.get("seller_type") == "OWNER":
        score += 0.2
    if metrics.get("is_shell_form"):
        score -= 0.8
    return _round_score(max(0.0, min(MAX_FLAGS, score)))


def compute_base_score(metrics: dict[str, Any]) -> dict[str, Any]:
    """Return base_score (0–8) and per-component breakdown."""
    components = {
        "price_per_sqm": _round_score(_score_price_per_sqm(metrics.get("price_per_sqm"))),
        "distance_to_gzira": _score_gzira(metrics.get("distance_to_gzira_km")),
        "sea_proximity": _score_sea(
            metrics.get("sea_proximity"),
            has_sea_view=bool(metrics.get("has_sea_view")),
        ),
        "area_sqm": _round_score(_score_area(metrics.get("area_sqm"))),
        "structured_flags": _score_flags(metrics),
    }
    base_score = _round_score(sum(components.values()))
    return {
        "base_score": base_score,
        "components": components,
    }


if __name__ == "__main__":
    samples = [
        {
            "price_per_sqm": 2600,
            "distance_to_gzira_km": 1.5,
            "sea_proximity": "nad_morzem",
            "area_sqm": 95,
            "has_sea_view": True,
            "is_freehold": True,
            "has_airspace": False,
            "seller_type": "OWNER",
            "is_shell_form": False,
        },
        {
            "price_per_sqm": None,
            "distance_to_gzira_km": 10.0,
            "sea_proximity": "daleko",
            "area_sqm": None,
            "has_sea_view": False,
            "is_freehold": False,
            "has_airspace": False,
            "seller_type": "AGENT",
            "is_shell_form": True,
        },
    ]
    for i, metrics in enumerate(samples, 1):
        result = compute_base_score(metrics)
        print(f"Sample {i}: base={result['base_score']} breakdown={result['components']}")
