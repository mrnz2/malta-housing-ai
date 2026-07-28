"""LLM investment evaluator using local Ollama (qwen2.5:7b)."""

from __future__ import annotations

import json
import re
from typing import Any

import ollama

from malta_housing.models import ParsedListing
from malta_housing.parsing.llm import clean_raw_text

MODEL_NAME = "qwen2.5:7b"
LLM_RETRIES = 3
OLLAMA_TIMEOUT_S = 120.0

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

    return {
        "price_eur": price,
        "locality": listing.locality,
        "property_type": listing.property_type,
        "bedrooms": listing.bedrooms,
        "seller_type": listing.seller_type,
        "distance_to_gzira_km": listing.distance_to_gzira_km,
        "area_sqm": area_sqm,
        "price_per_sqm": price_per_sqm,
        "is_freehold": listing.is_freehold,
        "has_airspace": listing.has_airspace,
        "has_sea_view": listing.has_sea_view,
        "is_shell_form": listing.is_shell_form,
        "key_features": listing.key_features,
    }


def _build_prompt(listing: ParsedListing, raw_text: str, metrics: dict[str, Any]) -> str:
    cleaned = clean_raw_text(raw_text)
    metrics_json = json.dumps(metrics, ensure_ascii=False, indent=2)

    return f"""
You are a Malta real-estate investment analyst. Evaluate this listing for buy-to-let /
long-term hold potential on mainland Malta (not Gozo).

Use the pre-calculated Python metrics below together with the raw listing text.
Be skeptical: flag shell form, emphyteusis/ground rent, missing lift, noise, dampness,
leasehold, agent fees, and unrealistic pricing.

PRE-CALCULATED METRICS (Python):
{metrics_json}

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
  "investment_score": 7.5,
  "pros": ["advantage 1", "advantage 2"],
  "cons": ["risk 1", "risk 2"],
  "summary": "Two concise sentences in English summarizing the investment case."
}}

Rules:
- investment_score: number from 0 (worst) to 10 (exceptional deal).
- pros: at most 3 short strings.
- cons: at most 3 short strings (risks/disadvantages).
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


def _validate_evaluation(data: dict[str, Any]) -> dict[str, Any]:
    if "investment_score" not in data:
        raise ValueError("missing investment_score")

    try:
        score = float(data["investment_score"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid investment_score: {data['investment_score']!r}") from exc

    score = max(0.0, min(10.0, score))
    if score == int(score):
        score = int(score)

    summary = str(data.get("summary", "")).strip()
    if not summary:
        raise ValueError("missing summary")

    return {
        "investment_score": score,
        "pros": _normalize_str_list(data.get("pros")),
        "cons": _normalize_str_list(data.get("cons")),
        "summary": summary,
    }


def _call_ollama(prompt: str) -> dict[str, Any]:
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
            return _validate_evaluation(parsed)
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
    """Score a parsed listing for investment potential via local Ollama."""
    if not raw_text or not raw_text.strip():
        raise ValueError("raw_text is required for evaluation")

    metrics = _compute_metrics(listing, raw_text)
    prompt = _build_prompt(listing, raw_text, metrics)
    result = _call_ollama(prompt)
    result["metrics"] = metrics
    return result
