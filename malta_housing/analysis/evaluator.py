"""Hybrid investment evaluator: deterministic base score + LLM qualitative adjustment."""

from __future__ import annotations

import json
import re
from typing import Any

import ollama

from malta_housing.analysis.scoring import compute_base_score
from malta_housing.distances import sea_proximity_for
from malta_housing.models import ParsedListing
from malta_housing.parsing.llm import clean_raw_text

MODEL_NAME = "qwen2.5:7b"
LLM_RETRIES = 3
OLLAMA_TIMEOUT_S = 120.0
LLM_ADJUSTMENT_MIN = -2.0
LLM_ADJUSTMENT_MAX = 2.0

_CLIENT = ollama.Client(timeout=OLLAMA_TIMEOUT_S)

_AREA_PATTERNS = (
    re.compile(
        r"(?:internal|total|gross)\s+area[:\s]*(\d+)\s*(?:sqm|sq\.?\s*m|m²|square\s*met(?:er|re)s?)",
        re.I,
    ),
    re.compile(
        r"(\d+)\s*(?:sqm|sq\.?\s*m|m²|square\s*met(?:er|re)s?)\s*(?:internal|total|gross)?",
        re.I,
    ),
)


def _extract_area_sqm(text: str) -> int | None:
    for pattern in _AREA_PATTERNS:
        match = pattern.search(text)
        if match:
            value = int(match.group(1))
            if 10 <= value <= 10_000:
                return value
    return None


def _compute_metrics(listing: ParsedListing, raw_text: str) -> dict[str, Any]:
    cleaned = clean_raw_text(raw_text)
    area_sqm = _extract_area_sqm(cleaned)
    price = listing.price_eur
    price_per_sqm: float | None = None
    if price is not None and area_sqm:
        price_per_sqm = round(price / area_sqm, 2)

    sea_proximity = listing.sea_proximity or sea_proximity_for(listing.locality)

    return {
        "price_eur": price,
        "locality": listing.locality,
        "property_type": listing.property_type,
        "bedrooms": listing.bedrooms,
        "seller_type": listing.seller_type,
        "distance_to_gzira_km": listing.distance_to_gzira_km,
        "sea_proximity": sea_proximity,
        "area_sqm": area_sqm,
        "price_per_sqm": price_per_sqm,
        "is_freehold": listing.is_freehold,
        "has_airspace": listing.has_airspace,
        "has_sea_view": listing.has_sea_view,
        "is_shell_form": listing.is_shell_form,
        "key_features": listing.key_features,
    }


def _build_prompt(
    listing: ParsedListing,
    raw_text: str,
    metrics: dict[str, Any],
    *,
    base_score: float,
    score_breakdown: dict[str, float],
) -> str:
    cleaned = clean_raw_text(raw_text)
    metrics_json = json.dumps(metrics, ensure_ascii=False, indent=2)
    breakdown_json = json.dumps(score_breakdown, ensure_ascii=False, indent=2)

    return f"""
You are a Malta real-estate investment analyst. A Python rubric has already scored
quantitative factors (price/m², Gżira distance, sea proximity, area, structural flags).
Your job is ONLY to adjust for qualitative risks and opportunities found in the listing text.

Do NOT re-score price, location, or sea proximity — those are already in base_score.

PRE-CALCULATED METRICS (Python):
{metrics_json}

BASE SCORE (already computed, max 8.0):
{base_score}

SCORE BREAKDOWN (do not repeat these factors in pros/cons unless adding new detail):
{breakdown_json}

STRUCTURED LISTING:
- Title: {listing.title}
- URL: {listing.url}
- Source: {listing.source or "unknown"}

RAW LISTING TEXT:
---
{cleaned[:8000]}
---

Return ONLY valid JSON with this exact shape:
{{
  "qualitative_adjustment": 0.5,
  "pros": ["advantage 1", "advantage 2"],
  "cons": ["risk 1", "risk 2"],
  "summary": "Two concise sentences in English summarizing the investment case."
}}

Rules:
- qualitative_adjustment: number from {LLM_ADJUSTMENT_MIN} to {LLM_ADJUSTMENT_MAX}.
  Use negative values for emphyteusis, ground rent, leasehold, shell finish issues,
  missing lift, noise, dampness, agent fees, unrealistic claims, renovation risk.
  Use positive values for exceptional layout, recent renovation, strong rental demand
  signals, or other upside not captured in base_score.
- pros: at most 3 short strings (qualitative only, not repeating base_score factors).
- cons: at most 3 short strings (risks/disadvantages from the text).
- summary: exactly two sentences in English.
"""


def _normalize_str_list(value: Any, *, max_items: int = 3) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def _clamp_adjustment(value: float) -> float:
    return max(LLM_ADJUSTMENT_MIN, min(LLM_ADJUSTMENT_MAX, round(value, 2)))


def _clamp_score(value: float) -> float:
    score = max(0.0, min(10.0, round(value, 2)))
    if score == int(score):
        return float(int(score))
    return score


def _validate_evaluation(
    data: dict[str, Any],
    *,
    base_score: float,
) -> dict[str, Any]:
    adjustment: float | None = None

    if "qualitative_adjustment" in data:
        try:
            adjustment = _clamp_adjustment(float(data["qualitative_adjustment"]))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid qualitative_adjustment: {data['qualitative_adjustment']!r}"
            ) from exc
    elif "investment_score" in data:
        try:
            llm_score = float(data["investment_score"])
            adjustment = _clamp_adjustment(llm_score - base_score)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid investment_score fallback: {data['investment_score']!r}"
            ) from exc
    else:
        raise ValueError("missing qualitative_adjustment")

    summary = str(data.get("summary", "")).strip()
    if not summary:
        raise ValueError("missing summary")

    investment_score = _clamp_score(base_score + adjustment)

    return {
        "investment_score": investment_score,
        "base_score": round(base_score, 2),
        "qualitative_adjustment": adjustment,
        "pros": _normalize_str_list(data.get("pros")),
        "cons": _normalize_str_list(data.get("cons")),
        "summary": summary,
    }


def _call_ollama(prompt: str, *, base_score: float) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, LLM_RETRIES + 1):
        try:
            response = _CLIENT.chat(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                format="json",
            )
            content = response["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError(f"expected JSON object, got {type(parsed).__name__}")
            return _validate_evaluation(parsed, base_score=base_score)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            last_error = exc
            print(f"   └─ Evaluation attempt {attempt}/{LLM_RETRIES} failed: {exc}")
        except Exception as exc:
            last_error = exc
            err_name = type(exc).__name__
            if "timeout" in str(exc).lower() or "Timeout" in err_name:
                print(f"   └─ Ollama timeout (attempt {attempt}/{LLM_RETRIES})")
            else:
                print(f"   └─ Evaluation attempt {attempt}/{LLM_RETRIES} failed: {exc}")
    raise RuntimeError(f"LLM evaluation failed after {LLM_RETRIES} attempts: {last_error}")


def evaluate_listing(listing: ParsedListing, raw_text: str) -> dict[str, Any]:
    """Score a parsed listing via deterministic base + LLM qualitative adjustment."""
    if not raw_text or not raw_text.strip():
        raise ValueError("raw_text is required for evaluation")

    metrics = _compute_metrics(listing, raw_text)
    scoring = compute_base_score(metrics)
    base_score = scoring["base_score"]
    score_breakdown = scoring["components"]

    prompt = _build_prompt(
        listing,
        raw_text,
        metrics,
        base_score=base_score,
        score_breakdown=score_breakdown,
    )
    result = _call_ollama(prompt, base_score=base_score)
    result["score_breakdown"] = score_breakdown
    result["metrics"] = metrics
    return result
