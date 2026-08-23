"""One-time backfill of listings.area_sqm for visible listings only.

Sources (best first):
  1. scraped raw_text (internal > total)
  2. key_features_en / key_features / key_features_pl
  3. title fields
  4. parsed_listings.json area_sqm
  5. evaluation cache

Hidden listings are skipped entirely.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from malta_housing.common import PARSED_PATH, STAGING_PATH, load_json_list
from malta_housing.db.store import _coerce_area_sqm
from malta_housing.parsing.area import (
    area_sqm_from_text,
    best_area_from_strings,
    extract_areas_from_text,
    resolve_area_sqm,
)
from malta_housing.paths import DB_PATH, ensure_data_dir

_VISIBLE = "(listings.is_hidden IS NULL OR listings.is_hidden = 0)"


def _json_features(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value.strip()]
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    return []


def _area_from_evaluation_row(row: sqlite3.Row) -> int | None:
    for key in ("metrics_area", "internal_area", "total_area"):
        area = _coerce_area_sqm(row[key])
        if area is not None:
            return area
    return None


def _resolve_best_area(
    *,
    raw_text: str,
    features_en: list[str],
    features: list[str],
    features_pl: list[str],
    titles: list[str],
    parsed_area: int | None,
    eval_area: int | None,
) -> tuple[int | None, str | None]:
    """Return the best (value, source) pair. Lower rank wins; internal beats total."""
    candidates: list[tuple[int, int, str]] = []

    def add(value: int | None, *, rank: int, source: str) -> None:
        coerced = _coerce_area_sqm(value)
        if coerced is not None:
            candidates.append((rank, coerced, source))

    for rank, source, strings in (
        (10, "key_features_en", features_en),
        (20, "key_features", features),
        (30, "key_features_pl", features_pl),
    ):
        add(best_area_from_strings(strings), rank=rank, source=source)

    if raw_text.strip():
        areas = extract_areas_from_text(raw_text)
        add(areas.get("internal_area_sqm"), rank=40, source="raw_text")
        add(areas.get("total_area_sqm"), rank=45, source="raw_text")

    add(best_area_from_strings(titles), rank=50, source="title")
    add(parsed_area, rank=60, source="parsed_json")
    add(eval_area, rank=70, source="evaluation")

    if not candidates:
        return None, None

    candidates.sort(key=lambda item: (item[0], item[1]))
    _rank, value, source = candidates[0]
    return value, source


def backfill_area_once(*, db_name: Path = DB_PATH, dry_run: bool = False) -> dict[str, int]:
    ensure_data_dir()
    staged = {
        item["url"]: item.get("raw_text") or ""
        for item in load_json_list(STAGING_PATH)
        if isinstance(item, dict) and item.get("url")
    }
    parsed = {
        item["url"]: _coerce_area_sqm(item.get("area_sqm"))
        for item in load_json_list(PARSED_PATH)
        if isinstance(item, dict) and item.get("url")
    }

    conn = sqlite3.connect(str(db_name))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    eval_areas: dict[str, int] = {}
    cursor.execute(
        """
        SELECT url,
               json_extract(evaluation_json, '$.metrics.area_sqm') AS metrics_area,
               json_extract(evaluation_json, '$.valuation_facts.internal_area_sqm') AS internal_area,
               json_extract(evaluation_json, '$.valuation_facts.total_area_sqm') AS total_area
        FROM evaluations
        """
    )
    for row in cursor.fetchall():
        area = _area_from_evaluation_row(row)
        if area is not None:
            eval_areas[row["url"]] = area

    cursor.execute(
        f"""
        SELECT id, url, area_sqm, key_features, key_features_en, key_features_pl,
               title, title_en, title_pl
        FROM listings
        WHERE {_VISIBLE}
        """
    )
    rows = cursor.fetchall()

    stats = {
        "visible_total": len(rows),
        "updated": 0,
        "unchanged": 0,
        "still_missing": 0,
        "skipped_hidden": 0,
        "from_raw_text": 0,
        "from_key_features_en": 0,
        "from_key_features": 0,
        "from_key_features_pl": 0,
        "from_title": 0,
        "from_parsed_json": 0,
        "from_evaluation": 0,
    }

    for row in rows:
        url = row["url"]
        current = _coerce_area_sqm(row["area_sqm"])
        best, source = _resolve_best_area(
            raw_text=staged.get(url, ""),
            features_en=_json_features(row["key_features_en"]),
            features=_json_features(row["key_features"]),
            features_pl=_json_features(row["key_features_pl"]),
            titles=[
                s
                for s in (row["title_en"], row["title"], row["title_pl"])
                if s and str(s).strip()
            ],
            parsed_area=parsed.get(url),
            eval_area=eval_areas.get(url),
        )

        if best is None:
            stats["still_missing"] += 1
            continue

        if best == current:
            stats["unchanged"] += 1
            continue

        if not dry_run:
            cursor.execute(
                "UPDATE listings SET area_sqm = ? WHERE id = ?",
                (best, row["id"]),
            )
        stats["updated"] += 1
        if source:
            stats[f"from_{source}"] += 1

    if not dry_run and stats["updated"]:
        conn.commit()
    conn.close()
    return stats


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    stats = backfill_area_once(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "DONE"
    print(f"📐 [{mode}] Visible listings: {stats['visible_total']}")
    print(
        f"   updated={stats['updated']} unchanged={stats['unchanged']} "
        f"still missing={stats['still_missing']}"
    )
    print(
        "   sources:"
        f" raw_text={stats['from_raw_text']}"
        f" key_features_en={stats['from_key_features_en']}"
        f" key_features={stats['from_key_features']}"
        f" key_features_pl={stats['from_key_features_pl']}"
        f" title={stats['from_title']}"
        f" parsed_json={stats['from_parsed_json']}"
        f" evaluation={stats['from_evaluation']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
