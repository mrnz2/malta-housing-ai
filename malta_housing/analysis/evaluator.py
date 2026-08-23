"""Hybrid investment evaluator: bank valuation + deterministic base score + LLM adjustment."""

from __future__ import annotations

import json
import re
from typing import Any

import ollama

from malta_housing.analysis.scoring import compute_base_score
from malta_housing.analysis.valuation import (
    apply_area_heuristics,
    compute_bank_valuation,
    normalize_valuation_facts,
)
from malta_housing.distances import sea_proximity_for
from malta_housing.models import ParsedListing
from malta_housing.parsing.area import extract_areas_from_text, valid_area
from malta_housing.parsing.llm import clean_raw_text

MODEL_NAME = "qwen2.5:7b"
LLM_RETRIES = 3
OLLAMA_TIMEOUT_S = 120.0
LLM_ADJUSTMENT_MIN = -2.0
LLM_ADJUSTMENT_MAX = 2.0

_CLIENT = ollama.Client(timeout=OLLAMA_TIMEOUT_S)

_FLOOR_PATTERN = re.compile(
    r"(?:floor|level|storey|story)[:\s#]*(\d+)|(\d+)(?:st|nd|rd|th)\s+floor",
    re.I,
)
_LIFT_PATTERN = re.compile(r"\b(?:lift|elevator)\b", re.I)
_NO_LIFT_PATTERN = re.compile(r"\bno\s+(?:lift|elevator)\b", re.I)
_OPTIONAL_GARAGE_PATTERN = re.compile(
    r"optional\s+garage|garage\s+(?:at\s+)?extra\s+price|garage\s+optional",
    re.I,
)
_INCLUDED_GARAGE_PATTERN = re.compile(
    r"(?:includes?|with)\s+(?:an?\s+)?(?:garage|car\s+space|parking\s+space)|garage\s+included",
    re.I,
)
_GROUND_RENT_PATTERN = re.compile(
    r"ground\s+rent|ċens|cens|perpetual\s+rent|temporary\s+rent|leasehold|emphyteusis",
    re.I,
)
_TEMPORARY_RENT_PATTERN = re.compile(
    r"temporary\s+(?:ground\s+)?rent|ground\s+rent.*\d+\s+years?\s+(?:left|remaining)",
    re.I,
)


def _extract_floor_level(text: str) -> int | None:
    match = _FLOOR_PATTERN.search(text)
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    if raw is None:
        return None
    try:
        floor = int(raw)
        if 0 <= floor <= 30:
            return floor
    except ValueError:
        return None
    return None


def _extract_lift(text: str) -> bool | None:
    if _NO_LIFT_PATTERN.search(text):
        return False
    if _LIFT_PATTERN.search(text):
        return True
    return None


def _extract_garage(text: str) -> dict[str, Any]:
    garage: dict[str, Any] = {
        "included_in_price": False,
        "optional_extra_price": False,
        "garage_price_eur": None,
    }
    if _OPTIONAL_GARAGE_PATTERN.search(text):
        garage["optional_extra_price"] = True
        return garage
    if _INCLUDED_GARAGE_PATTERN.search(text):
        garage["included_in_price"] = True
    return garage


def _extract_legal(text: str, listing: ParsedListing) -> dict[str, Any]:
    legal: dict[str, Any] = {
        "is_freehold": bool(listing.is_freehold),
        "has_ground_rent": False,
        "ground_rent_type": None,
        "ground_rent_annual_eur": None,
        "ground_rent_years_remaining": None,
        "has_emphyteusis": False,
    }
    lowered = text.lower()
    if _GROUND_RENT_PATTERN.search(text):
        legal["has_ground_rent"] = True
        if _TEMPORARY_RENT_PATTERN.search(text) or "temporary" in lowered:
            legal["ground_rent_type"] = "temporary"
        else:
            legal["ground_rent_type"] = "perpetual"
    if "emphyteusis" in lowered or "leasehold" in lowered:
        legal["has_emphyteusis"] = True
    years_match = re.search(
        r"(?:remaining|left)\s*[:\s]*(\d+)\s*years?",
        text,
        re.I,
    )
    if years_match:
        try:
            legal["ground_rent_years_remaining"] = int(years_match.group(1))
        except ValueError:
            pass
    annual_match = re.search(
        r"(?:ground\s+rent|ċens|cens)[^\d€]*€?\s*(\d+)",
        text,
        re.I,
    )
    if annual_match:
        try:
            legal["ground_rent_annual_eur"] = int(annual_match.group(1))
        except ValueError:
            pass
    return legal


def _extract_facts_from_regex(listing: ParsedListing, raw_text: str) -> dict[str, Any]:
    cleaned = clean_raw_text(raw_text)
    areas = extract_areas_from_text(cleaned)
    if listing.area_sqm is not None:
        areas["internal_area_sqm"] = listing.area_sqm
        if areas.get("total_area_sqm") is None:
            areas["total_area_sqm"] = listing.area_sqm
    return {
        **areas,
        "floor_level": _extract_floor_level(cleaned),
        "has_lift": _extract_lift(cleaned),
        "garage": _extract_garage(cleaned),
        "legal": _extract_legal(cleaned, listing),
        "has_sea_view": listing.has_sea_view,
        "has_airspace": listing.has_airspace,
        "is_shell_form": listing.is_shell_form,
        "sea_proximity": listing.sea_proximity or sea_proximity_for(listing.locality),
        "finish_level": "shell" if listing.is_shell_form else None,
        "micro_zone": "standard",
        "condition_issues": [],
    }


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        num = int(float(value))
        return num if valid_area(num) or 0 <= num <= 30 else None
    except (TypeError, ValueError):
        return None


def _merge_valuation_facts(regex_facts: dict[str, Any], llm_facts: dict[str, Any] | None) -> dict[str, Any]:
    llm = dict(llm_facts or {})
    merged = normalize_valuation_facts(regex_facts)

    for key in ("internal_area_sqm", "external_area_sqm", "total_area_sqm"):
        # Prefer structured scrape/regex areas over LLM guesses (avoids bad €/m²).
        regex_val = merged.get(key)
        llm_val = _coerce_int(llm.get(key))
        if regex_val is not None:
            merged[key] = regex_val
        elif llm_val is not None:
            merged[key] = llm_val

    llm_floor = _coerce_int(llm.get("floor_level"))
    if llm_floor is not None:
        merged["floor_level"] = llm_floor

    if llm.get("has_lift") is not None:
        merged["has_lift"] = bool(llm["has_lift"])
    if llm.get("finish_level"):
        merged["finish_level"] = str(llm["finish_level"]).strip().lower()
    if llm.get("micro_zone"):
        merged["micro_zone"] = str(llm["micro_zone"]).strip().lower()
    if isinstance(llm.get("condition_issues"), list):
        merged["condition_issues"] = [str(x).strip() for x in llm["condition_issues"] if str(x).strip()]

    garage = dict(merged.get("garage") or {})
    llm_garage = llm.get("garage")
    if isinstance(llm_garage, dict):
        for gkey in ("included_in_price", "optional_extra_price", "garage_price_eur"):
            if llm_garage.get(gkey) is not None:
                garage[gkey] = llm_garage[gkey]
    merged["garage"] = garage

    legal = dict(merged.get("legal") or {})
    llm_legal = llm.get("legal")
    if isinstance(llm_legal, dict):
        for lkey in legal:
            if llm_legal.get(lkey) is not None:
                legal[lkey] = llm_legal[lkey]
    merged["legal"] = legal

    apply_area_heuristics(merged)
    return merged


def _compute_metrics(
    listing: ParsedListing,
    raw_text: str,
    facts: dict[str, Any],
    bank_valuation: dict[str, Any],
) -> dict[str, Any]:
    internal = facts.get("internal_area_sqm")
    price = listing.price_eur
    price_per_sqm: float | None = None
    if price is not None and internal:
        try:
            price_per_sqm = round(price / float(internal), 2)
        except (TypeError, ValueError, ZeroDivisionError):
            price_per_sqm = None

    sea_proximity = listing.sea_proximity or sea_proximity_for(listing.locality)

    return {
        "price_eur": price,
        "locality": listing.locality,
        "property_type": listing.property_type,
        "bedrooms": listing.bedrooms,
        "seller_type": listing.seller_type,
        "distance_to_gzira_km": listing.distance_to_gzira_km,
        "sea_proximity": sea_proximity,
        "area_sqm": internal,
        "internal_area_sqm": internal,
        "external_area_sqm": facts.get("external_area_sqm"),
        "price_per_sqm": price_per_sqm,
        "is_freehold": listing.is_freehold,
        "has_airspace": listing.has_airspace,
        "has_sea_view": listing.has_sea_view,
        "is_shell_form": listing.is_shell_form,
        "ready": listing.ready,
        "key_features": listing.key_features,
        "bank_estimate_eur": bank_valuation.get("estimated_value_eur"),
        "bank_gap_pct": bank_valuation.get("gap_pct"),
        "bank_risk": bank_valuation.get("bank_risk"),
        "valuation_confidence": bank_valuation.get("confidence"),
    }


def _build_prompt(
    listing: ParsedListing,
    raw_text: str,
    metrics: dict[str, Any],
    bank_valuation: dict[str, Any],
    regex_facts: dict[str, Any],
    *,
    base_score: float,
    score_breakdown: dict[str, float],
) -> str:
    cleaned = clean_raw_text(raw_text)
    metrics_json = json.dumps(metrics, ensure_ascii=False, indent=2)
    breakdown_json = json.dumps(score_breakdown, ensure_ascii=False, indent=2)
    bank_json = json.dumps(bank_valuation, ensure_ascii=False, indent=2)
    regex_json = json.dumps(regex_facts, ensure_ascii=False, indent=2)

    return f"""
You are a Malta real-estate investment analyst working like a bank valuer.
Python has already computed a bank-style EUR valuation and a base investment score.
Extract factual details from the listing text and provide a small qualitative score adjustment.

PRE-CALCULATED METRICS (Python):
{metrics_json}

BANK VALUATION (Python, do NOT recalculate EUR):
{bank_json}

REGEX-EXTRACTED FACTS (verify/extend, do not contradict unless text is clear):
{regex_json}

BASE SCORE (already computed, max 8.0):
{base_score}

SCORE BREAKDOWN:
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
  "valuation_facts": {{
    "internal_area_sqm": 95,
    "external_area_sqm": 12,
    "total_area_sqm": null,
    "floor_level": 3,
    "has_lift": false,
    "finish_level": "finished",
    "micro_zone": "standard",
    "condition_issues": [],
    "garage": {{
      "included_in_price": false,
      "optional_extra_price": true,
      "garage_price_eur": 35000
    }},
    "legal": {{
      "is_freehold": true,
      "has_ground_rent": false,
      "ground_rent_type": null,
      "ground_rent_annual_eur": null,
      "ground_rent_years_remaining": null,
      "has_emphyteusis": false
    }}
  }},
  "qualitative_adjustment": 0.5,
  "summary_en": "Two concise English sentences summarizing the investment case.",
  "summary_pl": "Dwa zwięzłe zdania po polsku podsumowujące inwestycję.",
  "pros_en": ["advantage 1"],
  "pros_pl": ["zaleta 1"],
  "cons_en": ["risk 1"],
  "cons_pl": ["ryzyko 1"],
  "buyer_warnings_en": ["English warning before konvenju"],
  "buyer_warnings_pl": ["Ostrzeżenie po polsku przed konvenju"]
}}

Rules:
- Do NOT invent square meters — use null if not stated in text.
- Distinguish perpetual vs temporary ground rent (ċens). Temporary = high bank risk.
- "optional garage at extra price" => garage.included_in_price=false, optional_extra_price=true.
- micro_zone: one of seafront, premium, standard, quiet.
- finish_level: shell, finished, furnished, needs_renovation.
- qualitative_adjustment: number from {LLM_ADJUSTMENT_MIN} to {LLM_ADJUSTMENT_MAX}.
  Negative for PA issues, noise, hidden costs, unrealistic claims.
  Positive for recent renovation, exceptional layout, strong rental demand.
- pros/cons: at most 3 short strings each per language, qualitative only.
- buyer_warnings_en/pl: at most 3 short warnings before konvenju (ground rent, garage, area).
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

    summary_en = str(data.get("summary_en") or data.get("summary", "")).strip()
    summary_pl = str(data.get("summary_pl") or summary_en).strip()
    if not summary_en:
        raise ValueError("missing summary_en")

    investment_score = _clamp_score(base_score + adjustment)

    llm_facts = data.get("valuation_facts")
    if not isinstance(llm_facts, dict):
        llm_facts = {}

    pros_en = _normalize_str_list(data.get("pros_en") or data.get("pros"))
    pros_pl = _normalize_str_list(data.get("pros_pl") or pros_en)
    cons_en = _normalize_str_list(data.get("cons_en") or data.get("cons"))
    cons_pl = _normalize_str_list(data.get("cons_pl") or cons_en)
    warnings_en = _normalize_str_list(
        data.get("buyer_warnings_en"), max_items=5
    )
    warnings_pl = _normalize_str_list(
        data.get("buyer_warnings_pl") or data.get("buyer_warnings"), max_items=5
    )

    return {
        "investment_score": investment_score,
        "base_score": round(base_score, 2),
        "qualitative_adjustment": adjustment,
        "summary": summary_en,
        "summary_en": summary_en,
        "summary_pl": summary_pl,
        "pros": pros_en,
        "pros_en": pros_en,
        "pros_pl": pros_pl,
        "cons": cons_en,
        "cons_en": cons_en,
        "cons_pl": cons_pl,
        "buyer_warnings_en": warnings_en,
        "buyer_warnings_pl": warnings_pl,
        "llm_valuation_facts": llm_facts,
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


def _listing_dict(listing: ParsedListing) -> dict[str, Any]:
    return {
        "locality": listing.locality,
        "property_type": listing.property_type,
        "price_eur": listing.price_eur,
        "has_sea_view": listing.has_sea_view,
        "has_airspace": listing.has_airspace,
        "is_shell_form": listing.is_shell_form,
        "ready": listing.ready,
        "is_freehold": listing.is_freehold,
        "sea_proximity": listing.sea_proximity or sea_proximity_for(listing.locality),
    }


def evaluate_listing(listing: ParsedListing, raw_text: str) -> dict[str, Any]:
    """Score a parsed listing via bank valuation + base score + LLM qualitative adjustment."""
    if not raw_text or not raw_text.strip():
        raise ValueError("raw_text is required for evaluation")

    regex_facts = _extract_facts_from_regex(listing, raw_text)
    prelim_facts = _merge_valuation_facts(regex_facts, None)
    prelim_valuation = compute_bank_valuation(_listing_dict(listing), prelim_facts)

    prelim_metrics = _compute_metrics(listing, raw_text, prelim_facts, prelim_valuation)
    prelim_scoring = compute_base_score(prelim_metrics, prelim_valuation, prelim_facts)
    base_score_prelim = prelim_scoring["base_score"]
    breakdown_prelim = prelim_scoring["components"]

    prompt = _build_prompt(
        listing,
        raw_text,
        prelim_metrics,
        prelim_valuation,
        regex_facts,
        base_score=base_score_prelim,
        score_breakdown=breakdown_prelim,
    )
    llm_result = _call_ollama(prompt, base_score=base_score_prelim)

    final_facts = _merge_valuation_facts(regex_facts, llm_result.pop("llm_valuation_facts"))
    bank_valuation = compute_bank_valuation(_listing_dict(listing), final_facts)
    metrics = _compute_metrics(listing, raw_text, final_facts, bank_valuation)
    scoring = compute_base_score(metrics, bank_valuation, final_facts)
    base_score = scoring["base_score"]
    score_breakdown = scoring["components"]

    result = dict(llm_result)
    result["investment_score"] = _clamp_score(base_score + result["qualitative_adjustment"])
    result["base_score"] = round(base_score, 2)
    result["score_breakdown"] = score_breakdown
    result["metrics"] = metrics
    result["bank_valuation"] = bank_valuation
    result["valuation_facts"] = final_facts
    return result
