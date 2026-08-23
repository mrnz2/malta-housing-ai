"""Fetch, evaluate, rank, and report investment opportunities."""

from __future__ import annotations

import json
import time
from typing import Any

from malta_housing.analysis.evaluator import evaluate_listing
from malta_housing.common import STAGING_PATH, load_json_list
from malta_housing.db.store import (
    get_evaluated_urls,
    get_hidden_urls,
    init_db,
    save_evaluation,
    update_listing_editable,
)
from malta_housing.db.queries import (
    get_latest_scrape_day,
    get_listing_core_row,
    get_rank_candidates,
)
from malta_housing.i18n.localize import normalize_locale
from malta_housing.models import ParsedListing
from malta_housing.paths import DB_PATH

_SEA_LABELS = {
    "nad_morzem": "nad morzem",
    "blisko": "blisko",
    "daleko": "daleko",
}


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs:.1f}s"
    hours = int(minutes // 60)
    minutes = minutes % 60
    return f"{hours}h {minutes}m {secs:.0f}s"


def _load_raw_text_by_url() -> dict[str, str]:
    staging = load_json_list(STAGING_PATH)
    return {
        item["url"]: item.get("raw_text", "")
        for item in staging
        if item.get("url")
    }


def _listing_to_parsed(row: dict[str, Any]) -> ParsedListing:
    fields = {
        "url",
        "title",
        "price_eur",
        "locality",
        "property_type",
        "bedrooms",
        "area_sqm",
        "seller_type",
        "is_freehold",
        "has_airspace",
        "has_sea_view",
        "is_shell_form",
        "ready",
        "key_features",
        "source",
        "scraped_at",
        "updated_at",
        "distance_to_gzira_km",
        "sea_proximity",
    }
    return ParsedListing(**{k: row[k] for k in fields if k in row})


def _format_price(price: int | None) -> str:
    if price is None:
        return "€—"
    return f"€{price:,}"


def _format_sea_proximity(value: str | None) -> str:
    if not value:
        return "—"
    return _SEA_LABELS.get(value, str(value).replace("_", " "))


def _format_adjustment(value: float | int | None) -> str:
    if value is None:
        return "—"
    n = float(value)
    sign = "+" if n > 0 else ""
    return f"{sign}{n:g}"


def _score_line(row: dict[str, Any]) -> str:
    score = row.get("ai_score", row.get("investment_score", "?"))
    base = row.get("base_score")
    adj = row.get("qualitative_adjustment")
    if base is not None and adj is not None:
        return f"SCORE {score}/10 (base {base:g} + LLM {_format_adjustment(adj)})"
    return f"SCORE {score}/10"


def _valuation_line(row: dict[str, Any]) -> str | None:
    bank = row.get("bank_valuation")
    if not isinstance(bank, dict):
        payload = row.get("evaluation_json")
        if isinstance(payload, str) and payload.strip():
            try:
                data = json.loads(payload)
                bank = data.get("bank_valuation")
            except json.JSONDecodeError:
                bank = None
    if not isinstance(bank, dict):
        return None

    estimate = bank.get("estimated_value_eur")
    asking = bank.get("asking_price_eur") or row.get("price_eur")
    gap_pct = bank.get("gap_pct")
    risk = bank.get("bank_risk") or "—"
    confidence = bank.get("confidence") or "—"

    parts: list[str] = []
    if estimate is not None:
        low = bank.get("estimated_range_low_eur")
        high = bank.get("estimated_range_high_eur")
        if low is not None and high is not None:
            parts.append(f"Wycena: {_format_price(estimate)} ({_format_price(low)}–{_format_price(high)})")
        else:
            parts.append(f"Wycena: {_format_price(estimate)}")
    if asking is not None:
        parts.append(f"Cena: {_format_price(asking)}")
    if gap_pct is not None:
        sign = "+" if float(gap_pct) > 0 else ""
        parts.append(f"Luka: {sign}{gap_pct}%")
    parts.append(f"Ryzyko: {risk}")
    parts.append(f"Confidence: {confidence}")
    return " | ".join(parts)


def _sanity_line(row: dict[str, Any]) -> str | None:
    bank = row.get("bank_valuation")
    warnings = row.get("buyer_warnings_pl") or []
    if not isinstance(bank, dict):
        payload = row.get("evaluation_json")
        if isinstance(payload, str) and payload.strip():
            try:
                data = json.loads(payload)
                bank = data.get("bank_valuation")
                warnings = warnings or data.get("buyer_warnings_pl") or []
            except json.JSONDecodeError:
                bank = None
    if not isinstance(bank, dict):
        return None
    flags = bank.get("sanity_flags") or []
    bits = [str(x) for x in flags if x] + [str(x) for x in warnings if x]
    if not bits:
        return None
    return "⚠ " + "; ".join(bits[:3])


def _breakdown_line(row: dict[str, Any]) -> str | None:
    breakdown = row.get("score_breakdown")
    if not isinstance(breakdown, dict) or not breakdown:
        payload = row.get("evaluation_json")
        if isinstance(payload, str) and payload.strip():
            try:
                data = json.loads(payload)
                breakdown = data.get("score_breakdown")
            except json.JSONDecodeError:
                breakdown = None
    if not isinstance(breakdown, dict) or not breakdown:
        return None
    parts = [f"{key}: {value:g}" for key, value in breakdown.items()]
    return "Breakdown: " + ", ".join(parts)


def _print_report(ranked: list[dict[str, Any]], *, top: int, max_price: int | None) -> None:
    price_cap = f", max {_format_price(max_price)}" if max_price is not None else ""
    print()
    print("=" * 72)
    print(f" Malta Housing AI — Investment Rankings (top {top}{price_cap})")
    print("=" * 72)

    if not ranked:
        print("\nNo ranked listings found. Run scrape → parse → db first.")
        return

    for idx, row in enumerate(ranked[:top], 1):
        title = row.get("title") or "Untitled"
        locality = row.get("locality") or "—"
        prop_type = row.get("property_type") or "—"
        bedrooms = row.get("bedrooms")
        bed_txt = f"{bedrooms}-bed " if bedrooms is not None else ""
        dist = row.get("distance_to_gzira_km")
        dist_txt = f"→ Gżira: {dist} km" if dist is not None else "→ Gżira: —"
        sea_txt = f"Morze: {_format_sea_proximity(row.get('sea_proximity'))}"

        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        price_per_sqm = metrics.get("price_per_sqm")
        area_sqm = metrics.get("area_sqm")
        size_bits: list[str] = []
        if area_sqm is not None:
            size_bits.append(f"{area_sqm} m²")
        if price_per_sqm is not None:
            size_bits.append(f"€{price_per_sqm:,.0f}/m²")
        size_txt = " | ".join(size_bits) if size_bits else "—"

        flags: list[str] = []
        if row.get("is_freehold"):
            flags.append("Freehold")
        if row.get("has_airspace"):
            flags.append("Airspace")
        if row.get("has_sea_view"):
            flags.append("Sea view")
        if row.get("is_shell_form"):
            flags.append("Shell")
        if row.get("seller_type"):
            flags.append(str(row["seller_type"]))
        flags_txt = " | ".join(flags) if flags else "—"

        pros = row.get("pros") or []
        cons = row.get("cons") or []
        summary = row.get("ai_summary") or row.get("summary") or ""
        breakdown_txt = _breakdown_line(row)
        valuation_txt = _valuation_line(row)
        sanity_txt = _sanity_line(row)

        print()
        print(f" #{idx:<2} {_score_line(row)}  {_format_price(row.get('price_eur'))}  "
              f"{locality}  {bed_txt}{prop_type}")
        print(f"     {dist_txt}  |  {sea_txt}  |  {size_txt}")
        if valuation_txt:
            print(f"     {valuation_txt}")
        if sanity_txt:
            print(f"     {sanity_txt}")
        print(f"     {flags_txt}")
        if breakdown_txt:
            print(f"     {breakdown_txt}")
        if pros:
            print(f"     Pros: {'; '.join(pros)}")
        if cons:
            print(f"     Cons: {'; '.join(cons)}")
        if summary:
            print(f"     Summary: {summary}")
        print(f"     {row.get('url', '')}")

    print()
    print(f"Showing {min(top, len(ranked))} of {len(ranked)} evaluated listing(s).")
    print()


def _filter_latest_scrape(candidates: list[dict[str, Any]], latest_day: str | None) -> list[dict[str, Any]]:
    if not latest_day:
        return []
    return [
        row
        for row in candidates
        if row.get("scraped_at") and str(row["scraped_at"])[:10] == latest_day
    ]


def reevaluate_listing_by_id(
    listing_id: int,
    *,
    fields: dict[str, Any] | None = None,
    locale: str = "en",
    db_name: str | Any = DB_PATH,
) -> dict[str, Any]:
    """Re-run hybrid AI evaluation for one listing (current DB fields + staged raw_text)."""
    init_db(db_name)
    loc = normalize_locale(locale)
    if fields:
        if not update_listing_editable(listing_id, locale=loc, fields=fields, db_name=db_name):
            raise LookupError("Listing not found")

    row = get_listing_core_row(listing_id, db_name=db_name)
    if row is None:
        raise LookupError("Listing not found")

    url = row["url"]
    raw_text = _load_raw_text_by_url().get(url, "")
    if not raw_text.strip():
        raise ValueError("no_raw_text")

    listing = _listing_to_parsed(row)
    result = evaluate_listing(listing, raw_text)
    save_evaluation(url, result, db_name=db_name)
    return result


def run_rank(
    *,
    top: int = 10,
    max_price: int | None = None,
    source: str | None = None,
    force: bool = False,
    new_only: bool = False,
    db_name: str | Any = DB_PATH,
) -> list[dict[str, Any]]:
    """Evaluate unevaluated candidates, persist scores, return ranked rows."""
    init_db(db_name)
    candidates = get_rank_candidates(max_price=max_price, source=source, db_name=db_name)
    hidden_urls = get_hidden_urls(db_name=db_name)
    if hidden_urls:
        candidates = [c for c in candidates if c.get("url") not in hidden_urls]
    if new_only:
        latest_day = get_latest_scrape_day(db_name=db_name)
        candidates = _filter_latest_scrape(candidates, latest_day)
    if not candidates:
        print(f"⚠️ No candidate listings in {db_name}.")
        _print_report([], top=top, max_price=max_price)
        return []

    raw_by_url = _load_raw_text_by_url()
    already_evaluated = set() if force else get_evaluated_urls(db_name=db_name)

    to_evaluate = [
        c
        for c in candidates
        if c["url"] not in already_evaluated and c["url"] not in hidden_urls
    ]
    skipped = len(candidates) - len(to_evaluate)

    print(
        f"📊 Ranking: {len(candidates)} candidate(s)"
        + (f" [{source}]" if source else "")
        + (" [latest scrape]" if new_only else "")
        + f", {len(to_evaluate)} to evaluate, {skipped} cached."
    )
    if skipped and not force:
        print("   Tip: use --force to re-score with the hybrid rubric.")

    evaluated_ok = 0
    evaluated_fail = 0
    session_started = time.perf_counter()
    item_durations: list[float] = []

    if to_evaluate:
        started_at = time.strftime("%H:%M:%S")
        print(f"   Started at {started_at}")

    for i, row in enumerate(to_evaluate, 1):
        url = row["url"]
        item_started = time.perf_counter()
        if url in hidden_urls:
            elapsed = time.perf_counter() - item_started
            print(
                f"[{i}/{len(to_evaluate)}] Pominięto (ukryte): {row.get('title', url)} "
                f"({_format_duration(elapsed)})"
            )
            continue
        raw_text = raw_by_url.get(url, "")
        print(f"[{i}/{len(to_evaluate)}] Evaluating: {row.get('title', url)}...")
        if not raw_text.strip():
            elapsed = time.perf_counter() - item_started
            item_durations.append(elapsed)
            print(f"   └─ Skipped: no raw_text in scraped_listings.json ({_format_duration(elapsed)})")
            evaluated_fail += 1
            continue
        try:
            listing = _listing_to_parsed(row)
            result = evaluate_listing(listing, raw_text)
            save_evaluation(url, result, db_name=db_name)
            evaluated_ok += 1
            elapsed = time.perf_counter() - item_started
            item_durations.append(elapsed)
            print(
                f"   └─ Score: {result['investment_score']}/10 "
                f"(base {result['base_score']} + LLM {_format_adjustment(result['qualitative_adjustment'])}) "
                f"[{_format_duration(elapsed)}]"
            )
        except Exception as exc:
            elapsed = time.perf_counter() - item_started
            item_durations.append(elapsed)
            evaluated_fail += 1
            print(f"   └─ Error: {exc} [{_format_duration(elapsed)}]")

    if to_evaluate:
        session_elapsed = time.perf_counter() - session_started
        avg_txt = ""
        if item_durations:
            avg = sum(item_durations) / len(item_durations)
            avg_txt = f", avg {_format_duration(avg)}/item"
        print(
            f"\n✅ Evaluation session: {evaluated_ok} ok, {evaluated_fail} failed. "
            f"Total time: {_format_duration(session_elapsed)}{avg_txt}."
        )

    from malta_housing.db.queries import get_ranked_listings

    ranked = get_ranked_listings(
        max_price=max_price,
        limit=max(top, 100),
        db_name=db_name,
    )
    _print_report(ranked, top=top, max_price=max_price)
    return ranked
