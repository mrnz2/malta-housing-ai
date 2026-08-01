"""Translate existing English DB text fields to Polish via Ollama."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import ollama

from malta_housing.db.store import _connect, get_hidden_urls, init_db
from malta_housing.i18n.localize import _coerce_json_list
from malta_housing.paths import DB_PATH

MODEL_NAME = "SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0"
LLM_RETRIES = 3
OLLAMA_TIMEOUT_S = 120.0

_CLIENT = ollama.Client(timeout=OLLAMA_TIMEOUT_S)


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return len(value) == 0
    return False


def _en_title(row: dict[str, Any]) -> str:
    return str(row.get("title_en") or row.get("title") or "").strip()


def _en_features(row: dict[str, Any]) -> list[str]:
    raw = row.get("key_features_en")
    if _is_blank(raw):
        raw = row.get("key_features")
    return _coerce_json_list(raw)


def _en_summary(row: dict[str, Any]) -> str:
    return str(row.get("ai_summary_en") or row.get("ai_summary") or "").strip()


def _en_json_list(row: dict[str, Any], field: str) -> list[str]:
    raw = row.get(f"{field}_en")
    if _is_blank(raw):
        raw = row.get(field)
    return _coerce_json_list(raw)


def _needs_listing_translation(row: dict[str, Any], *, force: bool) -> bool:
    if force:
        return bool(_en_title(row) or _en_features(row))
    title_ok = not _is_blank(row.get("title_pl"))
    features_raw = row.get("key_features_pl")
    features_ok = not _is_blank(_coerce_json_list(features_raw))
    en_features = _en_features(row)
    if en_features and not features_ok:
        return True
    if _en_title(row) and not title_ok:
        return True
    return False


def _needs_evaluation_translation(row: dict[str, Any], *, force: bool) -> bool:
    if not row.get("eval_url"):
        return False
    if force:
        return bool(
            _en_summary(row)
            or _en_json_list(row, "pros")
            or _en_json_list(row, "cons")
            or _en_json_list(row, "buyer_warnings")
        )
    if _en_summary(row) and _is_blank(row.get("ai_summary_pl")):
        return True
    for field in ("pros", "cons", "buyer_warnings"):
        if _en_json_list(row, field) and _is_blank(_coerce_json_list(row.get(f"{field}_pl"))):
            return True
    return False


def _translate_payloads(
    row: dict[str, Any],
    *,
    listings: bool,
    evaluations: bool,
) -> list[dict[str, Any]]:
    """Split into smaller chunks so listing text can save even if evaluation fails."""
    chunks: list[dict[str, Any]] = []
    listing_payload: dict[str, Any] = {}
    eval_payload: dict[str, Any] = {}
    if listings:
        title = _en_title(row)
        if title:
            listing_payload["title_en"] = title
        features = _en_features(row)
        if features:
            listing_payload["key_features_en"] = features
    if evaluations and row.get("eval_url"):
        summary = _en_summary(row)
        if summary:
            eval_payload["summary_en"] = summary
        for field in ("pros", "cons", "buyer_warnings"):
            items = _en_json_list(row, field)
            if items:
                eval_payload[f"{field}_en"] = items
    if listing_payload:
        chunks.append(listing_payload)
    if eval_payload:
        chunks.append(eval_payload)
    return chunks


def _save_translation_result(
    listing_url: str,
    result: dict[str, Any],
    *,
    has_evaluation: bool,
    db_name: str | Path = DB_PATH,
) -> None:
    if "title_pl" in result or "key_features_pl" in result:
        _save_listing_pl(
            listing_url,
            title_pl=result.get("title_pl"),
            key_features_pl=result.get("key_features_pl"),
            db_name=db_name,
        )
    if has_evaluation and any(
        k in result for k in ("summary_pl", "pros_pl", "cons_pl", "buyer_warnings_pl")
    ):
        _save_evaluation_pl(
            listing_url,
            summary_pl=result.get("summary_pl"),
            pros_pl=result.get("pros_pl"),
            cons_pl=result.get("cons_pl"),
            buyer_warnings_pl=result.get("buyer_warnings_pl"),
            db_name=db_name,
        )


def _build_prompt(payload: dict[str, Any]) -> str:
    input_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return f"""
You are a professional translator for Malta real estate listings.
Translate the English property text below into natural Polish for a Polish-speaking buyer.

Rules:
- Keep Malta locality names unchanged (e.g. Sliema, Msida, St Paul's Bay).
- title_pl: short natural Polish property title (not word-for-word if awkward).
- key_features_pl: same number of items as key_features_en, each a concise Polish phrase.
- summary_pl: same meaning as summary_en, 1-2 sentences.
- pros_pl / cons_pl / buyer_warnings_pl: same count as English lists, short strings.
- Return ONLY valid JSON with Polish keys:
  title_pl, key_features_pl, summary_pl, pros_pl, cons_pl, buyer_warnings_pl
  (include only keys for fields present in INPUT).

INPUT:
{input_json}
"""


_STRING_PL_KEYS = frozenset({"title_pl", "summary_pl"})


def _coerce_translated_list(value: Any, expected_count: int | None = None) -> list[str]:
    """Parse list fields from LLM JSON; Bielik often returns comma-separated strings."""
    items = _coerce_json_list(value)
    if items:
        return items
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if expected_count == 1:
            return [text]
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if parts:
            return parts
    return []


def _normalize_output(data: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    mapping = {
        "title_en": "title_pl",
        "key_features_en": "key_features_pl",
        "summary_en": "summary_pl",
        "pros_en": "pros_pl",
        "cons_en": "cons_pl",
        "buyer_warnings_en": "buyer_warnings_pl",
    }
    for en_key, pl_key in mapping.items():
        if en_key not in payload:
            continue
        if pl_key not in data:
            raise ValueError(f"missing {pl_key}")
        value = data[pl_key]
        if pl_key in _STRING_PL_KEYS:
            text = str(value).strip()
            if not text:
                raise ValueError(f"empty {pl_key}")
            out[pl_key] = text
        else:
            en_items = _coerce_json_list(payload.get(en_key))
            expected_count = len(en_items) if en_items else None
            items = _coerce_translated_list(value, expected_count)
            if not items and en_key != "buyer_warnings_en":
                raise ValueError(f"empty {pl_key}")
            out[pl_key] = items
    return out


def _call_ollama(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = _build_prompt(payload)
    last_error: Exception | None = None
    for attempt in range(1, LLM_RETRIES + 1):
        try:
            response = _CLIENT.chat(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                format="json",
            )
            parsed = json.loads(response["message"]["content"])
            if not isinstance(parsed, dict):
                raise ValueError(f"expected JSON object, got {type(parsed).__name__}")
            return _normalize_output(parsed, payload)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            last_error = exc
            print(f"   └─ Translation attempt {attempt}/{LLM_RETRIES} failed: {exc}")
        except Exception as exc:
            last_error = exc
            print(f"   └─ Translation attempt {attempt}/{LLM_RETRIES} failed: {exc}")
    raise RuntimeError(f"LLM translation failed after {LLM_RETRIES} attempts: {last_error}")


def _save_listing_pl(
    url: str,
    *,
    title_pl: str | None = None,
    key_features_pl: list[str] | None = None,
    db_name: str | Path = DB_PATH,
) -> None:
    conn = _connect(db_name)
    cur = conn.cursor()
    if title_pl is not None:
        cur.execute(
            "UPDATE listings SET title_pl = ? WHERE url = ?",
            (title_pl, url),
        )
    if key_features_pl is not None:
        cur.execute(
            "UPDATE listings SET key_features_pl = ? WHERE url = ?",
            (json.dumps(key_features_pl, ensure_ascii=False), url),
        )
    conn.commit()
    conn.close()


def _save_evaluation_pl(
    url: str,
    *,
    summary_pl: str | None = None,
    pros_pl: list[str] | None = None,
    cons_pl: list[str] | None = None,
    buyer_warnings_pl: list[str] | None = None,
    db_name: str | Path = DB_PATH,
) -> None:
    conn = _connect(db_name)
    cur = conn.cursor()
    if summary_pl is not None:
        cur.execute(
            """
            UPDATE evaluations
            SET ai_summary_pl = ?
            WHERE url = ?
            """,
            (summary_pl, url),
        )
        cur.execute(
            """
            UPDATE listings
            SET ai_summary_pl = ?
            WHERE url = ?
            """,
            (summary_pl, url),
        )
    if pros_pl is not None:
        cur.execute(
            "UPDATE evaluations SET pros_pl = ? WHERE url = ?",
            (json.dumps(pros_pl, ensure_ascii=False), url),
        )
    if cons_pl is not None:
        cur.execute(
            "UPDATE evaluations SET cons_pl = ? WHERE url = ?",
            (json.dumps(cons_pl, ensure_ascii=False), url),
        )
    if buyer_warnings_pl is not None:
        cur.execute(
            "UPDATE evaluations SET buyer_warnings_pl = ? WHERE url = ?",
            (json.dumps(buyer_warnings_pl, ensure_ascii=False), url),
        )
    conn.commit()
    conn.close()


def _fetch_rows(
    *,
    url: str | None = None,
    db_name: str | Path = DB_PATH,
) -> list[dict[str, Any]]:
    conn = _connect(db_name)
    cur = conn.cursor()
    params: list[Any] = []
    where = ["(listings.is_hidden = 0 OR listings.is_hidden IS NULL)"]
    if url:
        where.append("listings.url = ?")
        params.append(url)
    cur.execute(
        f"""
        SELECT
            listings.id,
            listings.url,
            listings.title,
            listings.title_en,
            listings.title_pl,
            listings.key_features,
            listings.key_features_en,
            listings.key_features_pl,
            listings.ai_summary_en,
            listings.ai_summary_pl,
            e.url AS eval_url,
            e.ai_summary,
            e.ai_summary_en AS eval_ai_summary_en,
            e.ai_summary_pl AS eval_ai_summary_pl,
            e.pros,
            e.pros_en,
            e.pros_pl,
            e.cons,
            e.cons_en,
            e.cons_pl,
            e.buyer_warnings_en,
            e.buyer_warnings_pl
        FROM listings
        LEFT JOIN evaluations e ON e.url = listings.url
        WHERE {' AND '.join(where)}
        ORDER BY listings.id ASC
        """,
        params,
    )
    rows = []
    for raw in cur.fetchall():
        row = dict(raw)
        if row.get("eval_url"):
            row["ai_summary_en"] = row.get("eval_ai_summary_en") or row.get("ai_summary_en")
            row["ai_summary_pl"] = row.get("eval_ai_summary_pl") or row.get("ai_summary_pl")
        rows.append(row)
    conn.close()
    return rows


def run_translate(
    *,
    force: bool = False,
    listings: bool = True,
    evaluations: bool = True,
    url: str | None = None,
    db_name: str | Path = DB_PATH,
) -> dict[str, int]:
    """Translate English text columns to Polish without re-scraping or re-ranking."""
    init_db(db_name)
    hidden = get_hidden_urls(db_name)
    rows = _fetch_rows(url=url, db_name=db_name)

    to_process: list[dict[str, Any]] = []
    for row in rows:
        if row["url"] in hidden:
            continue
        need_listing = listings and _needs_listing_translation(row, force=force)
        need_eval = evaluations and _needs_evaluation_translation(row, force=force)
        if need_listing or need_eval:
            to_process.append(row)

    print(
        f"🌐 Translation EN→PL: {len(to_process)} listing(s) to process "
        f"(listings={listings}, evaluations={evaluations}, force={force})."
    )
    if not to_process:
        print("✅ Nothing to translate.")
        return {"ok": 0, "failed": 0, "skipped": len(rows)}

    ok = 0
    failed = 0
    interrupted = False
    started = time.perf_counter()

    for i, row in enumerate(to_process, 1):
        listing_url = row["url"]
        label = _en_title(row) or listing_url
        print(f"[{i}/{len(to_process)}] Translating: {label[:80]}...")
        try:
            chunks = _translate_payloads(row, listings=listings, evaluations=evaluations)
            if not chunks:
                print("   └─ Skipped: no English text to translate.")
                continue
            merged: dict[str, Any] = {}
            chunk_errors: list[str] = []
            for chunk in chunks:
                try:
                    merged.update(_call_ollama(chunk))
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    chunk_errors.append(str(exc))
                    print(f"   └─ Chunk failed ({', '.join(chunk.keys())}): {exc}")
            if not merged:
                failed += 1
                print("   └─ Error: all chunks failed")
                continue
            try:
                _save_translation_result(
                    listing_url,
                    merged,
                    has_evaluation=bool(row.get("eval_url")),
                    db_name=db_name,
                )
                ok += 1
                if chunk_errors:
                    print(f"   └─ Partial OK ({len(chunk_errors)} chunk(s) failed)")
                else:
                    print("   └─ OK")
            except Exception as exc:
                failed += 1
                print(f"   └─ Error saving: {exc}")
        except KeyboardInterrupt:
            interrupted = True
            print(f"\n⏹ Interrupted at [{i}/{len(to_process)}].")
            break

    elapsed = time.perf_counter() - started
    remaining = len(to_process) - ok - failed if interrupted else 0
    if interrupted:
        print(
            f"\n⏹ Translation stopped: {ok} ok, {failed} failed, "
            f"{remaining} not processed, {len(rows) - len(to_process)} skipped, {elapsed:.0f}s."
        )
        if not force:
            print("   Re-run the same command (without --force) to continue.")
        else:
            print("   Re-run to continue; use without --force to skip already translated fields.")
    else:
        print(
            f"\n✅ Translation done: {ok} ok, {failed} failed, "
            f"{len(rows) - len(to_process)} skipped, {elapsed:.0f}s."
        )
    return {
        "ok": ok,
        "failed": failed,
        "skipped": len(rows) - len(to_process),
        "interrupted": interrupted,
        "remaining": remaining,
    }
