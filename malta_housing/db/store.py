"""SQLite persistence with UPSERT and price history tracking."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from malta_housing.budget import MAX_PRICE_EUR, MIN_PRICE_EUR, is_out_of_budget
from malta_housing.common import PARSED_PATH, STAGING_PATH, load_json_list, purge_hidden_from_json, resolve_source
from malta_housing.distances import distance_to_gzira_km, sea_proximity_for
from malta_housing.geo import is_gozo_record
from malta_housing.i18n.localize import normalize_locale
from malta_housing.i18n.property_types import normalize_property_type
from malta_housing.models import utc_now_iso
from malta_housing.parsing.area import area_sqm_from_text
from malta_housing.paths import DB_PATH, ensure_data_dir

_LISTINGS_I18N_COLUMNS = (
    ("title_en", "TEXT"),
    ("title_pl", "TEXT"),
    ("key_features_en", "TEXT"),
    ("key_features_pl", "TEXT"),
    ("ai_summary_en", "TEXT"),
    ("ai_summary_pl", "TEXT"),
)

_EVALUATIONS_I18N_COLUMNS = (
    ("ai_summary_en", "TEXT"),
    ("ai_summary_pl", "TEXT"),
    ("pros_en", "TEXT"),
    ("pros_pl", "TEXT"),
    ("cons_en", "TEXT"),
    ("cons_pl", "TEXT"),
    ("buyer_warnings_en", "TEXT"),
    ("buyer_warnings_pl", "TEXT"),
)


def _connect(db_name: str | Path = DB_PATH) -> sqlite3.Connection:
    ensure_data_dir()
    conn = sqlite3.connect(str(db_name), timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(cursor: sqlite3.Cursor, table: str, column: str, col_def: str) -> None:
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


def init_db(db_name: str | Path = DB_PATH) -> None:
    """Create tables and migrate missing columns. Does not require parsed JSON."""
    conn = _connect(db_name)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT,
            price_eur INTEGER,
            locality TEXT,
            property_type TEXT,
            bedrooms INTEGER,
            seller_type TEXT,
            is_freehold BOOLEAN,
            has_airspace BOOLEAN,
            has_sea_view BOOLEAN,
            is_shell_form BOOLEAN,
            ready BOOLEAN,
            key_features TEXT,
            source TEXT,
            scraped_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_hidden BOOLEAN DEFAULT 0,
            notes TEXT
        )
        """
    )

    for column, col_def in (
        ("source", "TEXT"),
        ("scraped_at", "TIMESTAMP"),
        ("updated_at", "TIMESTAMP"),
        ("distance_to_gzira_km", "REAL"),
        ("sea_proximity", "TEXT"),
        ("is_hidden", "BOOLEAN DEFAULT 0"),
        ("notes", "TEXT"),
        ("ai_score", "REAL"),
        ("ai_summary", "TEXT"),
        ("ai_evaluated_at", "TIMESTAMP"),
        ("ready", "BOOLEAN"),
        ("is_fav", "BOOLEAN DEFAULT 0"),
        ("area_sqm", "INTEGER"),
    ):
        _ensure_column(cursor, "listings", column, col_def)

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            price_eur INTEGER,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (url) REFERENCES listings(url)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_history_url ON price_history(url)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            ai_score REAL NOT NULL,
            ai_summary TEXT,
            pros TEXT,
            cons TEXT,
            evaluation_json TEXT,
            evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (url) REFERENCES listings(url)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_evaluations_score ON evaluations(ai_score DESC)"
    )

    for column, col_def in _LISTINGS_I18N_COLUMNS:
        _ensure_column(cursor, "listings", column, col_def)
    for column, col_def in _EVALUATIONS_I18N_COLUMNS:
        _ensure_column(cursor, "evaluations", column, col_def)

    conn.commit()
    _backfill_locality_fields(conn)
    _backfill_sources(conn)
    _backfill_listing_scores(conn)
    _backfill_bilingual_columns(conn)
    _backfill_property_type_codes(conn)
    _backfill_area_sqm(conn)
    conn.close()


def _backfill_property_type_codes(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, property_type FROM listings WHERE property_type IS NOT NULL"
        )
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        return
    updated = 0
    for row in rows:
        normalized = normalize_property_type(row["property_type"])
        if normalized and normalized != row["property_type"]:
            cursor.execute(
                "UPDATE listings SET property_type = ? WHERE id = ?",
                (normalized, row["id"]),
            )
            updated += 1
    if updated:
        conn.commit()
        print(f"🏷️ Znormalizowano property_type dla {updated} ofert.")


def _coerce_area_sqm(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        area = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    if area < 10 or area > 10_000:
        return None
    return area


def _area_from_evaluation(evaluation: dict[str, Any] | None) -> int | None:
    if not evaluation:
        return None
    metrics = evaluation.get("metrics") if isinstance(evaluation.get("metrics"), dict) else {}
    facts = (
        evaluation.get("valuation_facts")
        if isinstance(evaluation.get("valuation_facts"), dict)
        else {}
    )
    for raw in (
        metrics.get("area_sqm"),
        facts.get("internal_area_sqm"),
        facts.get("total_area_sqm"),
    ):
        area = _coerce_area_sqm(raw)
        if area is not None:
            return area
    return None


def backfill_area_sqm(
    conn: sqlite3.Connection | None = None,
    *,
    db_name: str | Path = DB_PATH,
) -> dict[str, int]:
    """Fill listings.area_sqm from scraped listing text (regex on raw_text).

    Prefers area parsed from staging JSON; falls back to evaluation metrics when
    staging text is missing. Never clears an existing non-null area_sqm.
    """
    own_conn = conn is None
    if own_conn:
        ensure_data_dir()
        conn = sqlite3.connect(str(db_name))
        conn.row_factory = sqlite3.Row

    cursor = conn.cursor()
    stats = {
        "total": 0,
        "updated": 0,
        "from_text": 0,
        "from_evaluation": 0,
        "already_set": 0,
        "still_missing": 0,
        "no_staging_text": 0,
    }
    try:
        cursor.execute("SELECT id, url, area_sqm FROM listings")
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        if own_conn:
            conn.close()
        return stats

    staged = {
        item["url"]: item.get("raw_text") or ""
        for item in load_json_list(STAGING_PATH)
        if isinstance(item, dict) and item.get("url")
    }

    eval_areas: dict[str, int] = {}
    try:
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
            area = (
                _coerce_area_sqm(row["metrics_area"])
                or _coerce_area_sqm(row["internal_area"])
                or _coerce_area_sqm(row["total_area"])
            )
            if area is not None:
                eval_areas[row["url"]] = area
    except sqlite3.OperationalError:
        pass

    stats["total"] = len(rows)
    for row in rows:
        url = row["url"]
        current = _coerce_area_sqm(row["area_sqm"])
        raw_text = staged.get(url, "")
        if not raw_text.strip():
            stats["no_staging_text"] += 1

        desired: int | None = None
        source: str | None = None

        if raw_text.strip():
            from_text = area_sqm_from_text(raw_text)
            if from_text is not None:
                desired = from_text
                source = "text"

        if desired is None and current is None:
            eval_area = eval_areas.get(url)
            if eval_area is not None:
                desired = eval_area
                source = "evaluation"

        if desired is None:
            if current is not None:
                stats["already_set"] += 1
            else:
                stats["still_missing"] += 1
            continue

        if desired == current:
            stats["already_set"] += 1
            continue

        cursor.execute(
            "UPDATE listings SET area_sqm = ? WHERE id = ?",
            (desired, row["id"]),
        )
        stats["updated"] += 1
        if source == "text":
            stats["from_text"] += 1
        elif source == "evaluation":
            stats["from_evaluation"] += 1

    if stats["updated"]:
        conn.commit()

    if own_conn:
        conn.close()
    return stats


def _backfill_area_sqm(conn: sqlite3.Connection) -> None:
    stats = backfill_area_sqm(conn)
    if stats["updated"]:
        print(f"📐 Uzupełniono/poprawiono area_sqm dla {stats['updated']} ofert.")


def _json_list_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, list):
        if not value:
            return None
        return json.dumps(value, ensure_ascii=False)
    return None


def _backfill_bilingual_columns(conn: sqlite3.Connection) -> None:
    """Copy legacy single-language columns into *_en where bilingual columns are empty."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE listings SET
                title_en = title
            WHERE (title_en IS NULL OR TRIM(title_en) = '')
              AND title IS NOT NULL AND TRIM(title) != ''
            """
        )
        cursor.execute(
            """
            UPDATE listings SET
                key_features_en = key_features
            WHERE (key_features_en IS NULL OR TRIM(key_features_en) = '')
              AND key_features IS NOT NULL AND TRIM(key_features) != ''
            """
        )
        cursor.execute(
            """
            UPDATE listings SET
                ai_summary_en = ai_summary
            WHERE (ai_summary_en IS NULL OR TRIM(ai_summary_en) = '')
              AND ai_summary IS NOT NULL AND TRIM(ai_summary) != ''
            """
        )
        cursor.execute(
            """
            UPDATE evaluations SET
                ai_summary_en = ai_summary
            WHERE (ai_summary_en IS NULL OR TRIM(ai_summary_en) = '')
              AND ai_summary IS NOT NULL AND TRIM(ai_summary) != ''
            """
        )
        cursor.execute(
            """
            UPDATE evaluations SET
                pros_en = pros
            WHERE (pros_en IS NULL OR TRIM(pros_en) = '')
              AND pros IS NOT NULL AND TRIM(pros) != ''
            """
        )
        cursor.execute(
            """
            UPDATE evaluations SET
                cons_en = cons
            WHERE (cons_en IS NULL OR TRIM(cons_en) = '')
              AND cons IS NOT NULL AND TRIM(cons) != ''
            """
        )
        cursor.execute(
            """
            UPDATE evaluations
            SET buyer_warnings_pl = json_extract(evaluation_json, '$.buyer_warnings_pl')
            WHERE (buyer_warnings_pl IS NULL OR TRIM(buyer_warnings_pl) = '')
              AND evaluation_json IS NOT NULL
              AND json_extract(evaluation_json, '$.buyer_warnings_pl') IS NOT NULL
            """
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _backfill_sources(conn: sqlite3.Connection) -> int:
    """Fill missing source from listing URL host."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, url FROM listings
            WHERE source IS NULL OR TRIM(source) = ''
            """
        )
    except sqlite3.OperationalError:
        return 0

    updated = 0
    for row in cursor.fetchall():
        source = resolve_source(None, row["url"])
        if source is None:
            continue
        cursor.execute(
            "UPDATE listings SET source = ? WHERE id = ?",
            (source, row["id"]),
        )
        updated += 1
    if updated:
        conn.commit()
        print(f"🏷️ Uzupełniono source dla {updated} ofert.")
    return updated


def _backfill_locality_fields(conn: sqlite3.Connection) -> int:
    """Fill missing distance_to_gzira_km and sea_proximity from locality + to_gzira.csv."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, locality FROM listings
            WHERE (
                distance_to_gzira_km IS NULL
                OR sea_proximity IS NULL
                OR TRIM(sea_proximity) = ''
            )
              AND locality IS NOT NULL
              AND TRIM(locality) != ''
            """
        )
    except sqlite3.OperationalError:
        return 0

    updated = 0
    for row in cursor.fetchall():
        locality = row["locality"]
        km = distance_to_gzira_km(locality)
        sea = sea_proximity_for(locality)
        if km is None and sea is None:
            continue
        cursor.execute(
            """
            UPDATE listings
            SET distance_to_gzira_km = COALESCE(?, distance_to_gzira_km),
                sea_proximity = COALESCE(?, sea_proximity)
            WHERE id = ?
            """,
            (km, sea, row["id"]),
        )
        updated += 1
    if updated:
        conn.commit()
        print(f"📍 Uzupełniono pola miejscowości dla {updated} ofert.")
    return updated


def _backfill_listing_scores(conn: sqlite3.Connection) -> int:
    """Copy ai_score / ai_summary from evaluations into listings when missing."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE listings
            SET
                ai_score = (
                    SELECT e.ai_score FROM evaluations e WHERE e.url = listings.url
                ),
                ai_summary = (
                    SELECT e.ai_summary FROM evaluations e WHERE e.url = listings.url
                ),
                ai_evaluated_at = (
                    SELECT e.evaluated_at FROM evaluations e WHERE e.url = listings.url
                )
            WHERE url IN (SELECT url FROM evaluations)
              AND (
                    ai_score IS NULL
                    OR ai_summary IS NULL
                    OR ai_evaluated_at IS NULL
              )
            """
        )
        updated = cursor.rowcount
    except sqlite3.OperationalError:
        return 0

    if updated:
        conn.commit()
        print(f"📊 Uzupełniono ai_score dla {updated} ofert w listings.")
    return updated


def get_known_urls(db_name: str | Path = DB_PATH) -> set[str]:
    """Return URLs already present in the listings table."""
    db_path = Path(db_name)
    if not db_path.exists():
        return set()
    conn = _connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT url FROM listings")
        urls = {row["url"] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        urls = set()
    conn.close()
    return urls


def get_hidden_urls(db_name: str | Path = DB_PATH) -> set[str]:
    """Return URLs marked hidden in the listings table (skip parse/rank/scrape)."""
    db_path = Path(db_name)
    if not db_path.exists():
        return set()
    conn = _connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT url FROM listings WHERE is_hidden = 1")
        urls = {row["url"] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        urls = set()
    conn.close()
    return urls


def delete_gozo_listings(db_name: str | Path = DB_PATH) -> dict[str, int]:
    """Remove Gozo properties (and their price history) from the database."""
    if not Path(db_name).exists():
        print(f"⚠️ Brak bazy {db_name} — nic do usunięcia.")
        return {"deleted": 0}

    init_db(db_name)
    conn = _connect(db_name)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, url, title, locality FROM listings
        """
    )
    to_delete: list[tuple[int, str]] = []
    for row in cursor.fetchall():
        if is_gozo_record(
            {"title": row["title"], "locality": row["locality"], "url": row["url"]}
        ):
            to_delete.append((row["id"], row["url"]))

    for _listing_id, url in to_delete:
        cursor.execute("DELETE FROM price_history WHERE url = ?", (url,))
        cursor.execute("DELETE FROM listings WHERE url = ?", (url,))

    conn.commit()
    conn.close()
    print(f"🗑️ Usunięto {len(to_delete)} ofert z Gozo z '{db_name}'.")
    return {"deleted": len(to_delete)}


def delete_out_of_budget_listings(
    db_name: str | Path = DB_PATH,
    *,
    min_price_eur: int = MIN_PRICE_EUR,
    max_price_eur: int = MAX_PRICE_EUR,
) -> dict[str, int]:
    """Remove listings priced below min or above max (and their price history)."""
    if not Path(db_name).exists():
        print(f"⚠️ Brak bazy {db_name} — nic do usunięcia.")
        return {"deleted": 0, "below_min": 0, "above_max": 0}

    init_db(db_name)
    conn = _connect(db_name)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, url, price_eur FROM listings
        WHERE price_eur IS NOT NULL
          AND (price_eur < ? OR price_eur > ?)
        """,
        (min_price_eur, max_price_eur),
    )
    rows = cursor.fetchall()
    below_min = sum(1 for row in rows if row["price_eur"] < min_price_eur)
    above_max = sum(1 for row in rows if row["price_eur"] > max_price_eur)

    for row in rows:
        cursor.execute("DELETE FROM price_history WHERE url = ?", (row["url"],))
        cursor.execute("DELETE FROM listings WHERE id = ?", (row["id"],))

    conn.commit()
    conn.close()
    print(
        f"🗑️ Usunięto {len(rows)} ofert poza budżetem €{min_price_eur:,}–€{max_price_eur:,} "
        f"z '{db_name}' (<min: {below_min}, >max: {above_max})."
    )
    return {
        "deleted": len(rows),
        "below_min": below_min,
        "above_max": above_max,
    }


def clear_evaluations(db_name: str | Path = DB_PATH) -> dict[str, int]:
    """Remove all AI scores/evaluations; listings keep ai_score fields NULL."""
    if not Path(db_name).exists():
        print(f"⚠️ Brak bazy {db_name} — nic do wyczyszczenia.")
        return {"evaluations_deleted": 0, "listings_cleared": 0}

    init_db(db_name)
    conn = _connect(db_name)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM evaluations")
    eval_count = int(cursor.fetchone()[0])
    cursor.execute(
        """
        SELECT COUNT(*) FROM listings
        WHERE ai_score IS NOT NULL
           OR ai_summary IS NOT NULL
           OR ai_evaluated_at IS NOT NULL
        """
    )
    listings_cleared = int(cursor.fetchone()[0])
    cursor.execute("DELETE FROM evaluations")
    cursor.execute(
        """
        UPDATE listings
        SET ai_score = NULL, ai_summary = NULL, ai_summary_en = NULL, ai_summary_pl = NULL,
            ai_evaluated_at = NULL
        """
    )
    conn.commit()
    conn.close()
    print(
        f"🧹 Wyczyszczono oceny AI: {eval_count} ewaluacji, "
        f"{listings_cleared} ofert w listings."
    )
    return {"evaluations_deleted": eval_count, "listings_cleared": listings_cleared}


def set_listing_hidden(
    listing_id: int, hidden: bool, db_name: str | Path = DB_PATH
) -> bool:
    """Set is_hidden for a listing by id. Returns False if the id was not found."""
    init_db(db_name)
    conn = _connect(db_name)
    cur = conn.cursor()
    cur.execute(
        "UPDATE listings SET is_hidden = ? WHERE id = ?",
        (1 if hidden else 0, listing_id),
    )
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def set_listing_fav(
    listing_id: int, fav: bool, db_name: str | Path = DB_PATH
) -> bool:
    """Set is_fav for a listing by id. Returns False if the id was not found."""
    init_db(db_name)
    conn = _connect(db_name)
    cur = conn.cursor()
    cur.execute(
        "UPDATE listings SET is_fav = ? WHERE id = ?",
        (1 if fav else 0, listing_id),
    )
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def set_listing_notes(
    listing_id: int, notes: str | None, db_name: str | Path = DB_PATH
) -> bool:
    """Set notes for a listing by id. Empty/whitespace becomes NULL. Returns False if missing."""
    init_db(db_name)
    cleaned = (notes or "").strip() or None
    conn = _connect(db_name)
    cur = conn.cursor()
    cur.execute(
        "UPDATE listings SET notes = ? WHERE id = ?",
        (cleaned, listing_id),
    )
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def set_listing_ready(
    listing_id: int, ready: bool | None, db_name: str | Path = DB_PATH
) -> bool:
    """Set ready for a listing by id. None clears the value. Returns False if missing."""
    init_db(db_name)
    conn = _connect(db_name)
    cur = conn.cursor()
    cur.execute(
        "UPDATE listings SET ready = ? WHERE id = ?",
        (ready, listing_id),
    )
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        items = [line.strip() for line in value.splitlines() if line.strip()]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        return None
    return items


def update_listing_editable(
    listing_id: int,
    *,
    locale: str,
    fields: dict[str, Any],
    db_name: str | Path = DB_PATH,
) -> bool:
    """Apply manual UI edits to a listing (locale-specific text + shared scalars)."""
    init_db(db_name)
    loc = normalize_locale(locale)
    conn = _connect(db_name)
    try:
        cur = conn.cursor()
        cur.execute("SELECT url, locality FROM listings WHERE id = ?", (listing_id,))
        row = cur.fetchone()
        if not row:
            return False

        url = row["url"]
        listing_sets: list[str] = []
        listing_params: list[Any] = []
        now = utc_now_iso()

        def add_listing(column: str, value: Any) -> None:
            listing_sets.append(f"{column} = ?")
            listing_params.append(value)

        if "title" in fields:
            title = _clean_text(fields["title"])
            add_listing(f"title_{loc}", title)
            if loc == "en":
                add_listing("title", title)

        if "key_features" in fields:
            features = _clean_str_list(fields["key_features"])
            features_json = _json_list_text(features) if features is not None else None
            add_listing(f"key_features_{loc}", features_json)
            if loc == "en":
                add_listing("key_features", features_json)

        if "ai_summary" in fields:
            summary = _clean_text(fields["ai_summary"])
            add_listing(f"ai_summary_{loc}", summary)
            if loc == "en":
                add_listing("ai_summary", summary)

        if "price_eur" in fields:
            price = fields["price_eur"]
            if price is None or price == "":
                add_listing("price_eur", None)
            else:
                add_listing("price_eur", int(price))

        if "locality" in fields:
            locality = _clean_text(fields["locality"])
            add_listing("locality", locality)
            if locality:
                km = distance_to_gzira_km(locality)
                sea = sea_proximity_for(locality)
                add_listing("distance_to_gzira_km", km)
                add_listing("sea_proximity", sea)

        if "property_type" in fields:
            raw_type = _clean_text(fields["property_type"])
            add_listing(
                "property_type",
                normalize_property_type(raw_type) or raw_type,
            )

        if "bedrooms" in fields:
            bedrooms = fields["bedrooms"]
            if bedrooms is None or bedrooms == "":
                add_listing("bedrooms", None)
            else:
                add_listing("bedrooms", int(bedrooms))

        if "area_sqm" in fields:
            add_listing("area_sqm", _coerce_area_sqm(fields["area_sqm"]))

        if "seller_type" in fields:
            add_listing("seller_type", _clean_text(fields["seller_type"]))

        if "url" in fields:
            add_listing("url", _clean_text(fields["url"]))

        if "notes" in fields:
            add_listing("notes", _clean_text(fields["notes"]))

        if "ready" in fields:
            add_listing("ready", fields["ready"])

        for flag in ("is_freehold", "has_airspace", "has_sea_view", "is_shell_form", "is_hidden"):
            if flag in fields:
                add_listing(flag, 1 if bool(fields[flag]) else 0)

        if listing_sets:
            listing_sets.append("updated_at = ?")
            listing_params.append(now)
            listing_params.append(listing_id)
            cur.execute(
                f"UPDATE listings SET {', '.join(listing_sets)} WHERE id = ?",
                listing_params,
            )

        eval_sets: list[str] = []
        eval_params: list[Any] = []

        def add_eval(column: str, value: Any) -> None:
            eval_sets.append(f"{column} = ?")
            eval_params.append(value)

        if "ai_summary" in fields:
            add_eval(f"ai_summary_{loc}", _clean_text(fields["ai_summary"]))
            if loc == "en":
                add_eval("ai_summary", _clean_text(fields["ai_summary"]))

        for list_field in ("pros", "cons", "buyer_warnings"):
            if list_field not in fields:
                continue
            items = _clean_str_list(fields[list_field])
            json_value = _json_list_text(items) if items is not None else "[]"
            if list_field == "buyer_warnings":
                add_eval(f"buyer_warnings_{loc}", json_value)
            else:
                add_eval(f"{list_field}_{loc}", json_value)
                if loc == "en":
                    add_eval(list_field, json_value)

        if eval_sets:
            eval_params.append(url)
            cur.execute(
                f"UPDATE evaluations SET {', '.join(eval_sets)} WHERE url = ?",
                eval_params,
            )

        conn.commit()
        return True
    finally:
        conn.close()


def save_listings_to_db(
    listings: list[dict[str, Any]], db_name: str | Path = DB_PATH
) -> dict[str, int]:
    """Insert new listings or update existing ones; record price changes."""
    init_db(db_name)
    conn = _connect(db_name)
    cursor = conn.cursor()

    inserted = 0
    updated = 0
    price_changes = 0
    skipped_gozo = 0
    skipped_budget = 0
    skipped_hidden = 0
    now = utc_now_iso()

    for item in listings:
        if is_gozo_record(item):
            skipped_gozo += 1
            continue
        if is_out_of_budget(item):
            skipped_budget += 1
            continue

        url = item["url"]
        cursor.execute(
            "SELECT price_eur, title, is_hidden FROM listings WHERE url = ?",
            (url,),
        )
        existing = cursor.fetchone()
        if existing is not None and existing["is_hidden"]:
            skipped_hidden += 1
            continue
        key_features_en = _json_list_text(
            item.get("key_features_en") or item.get("key_features")
        )
        key_features_pl = _json_list_text(item.get("key_features_pl"))
        title_en = item.get("title_en") or item.get("title")
        title_pl = item.get("title_pl")
        property_type = normalize_property_type(item.get("property_type")) or item.get(
            "property_type"
        )

        distance_km = item.get("distance_to_gzira_km")
        if distance_km is None:
            distance_km = distance_to_gzira_km(item.get("locality"))

        sea_proximity = item.get("sea_proximity")
        if not sea_proximity:
            sea_proximity = sea_proximity_for(item.get("locality"))

        source = resolve_source(item.get("source"), url)

        ready = item.get("ready")
        if ready is not None:
            ready = bool(ready)

        area_sqm = _coerce_area_sqm(item.get("area_sqm"))

        values = (
            title_en,
            title_en,
            title_pl,
            item.get("price_eur"),
            item.get("locality"),
            property_type,
            item.get("bedrooms"),
            area_sqm,
            item.get("seller_type"),
            item.get("is_freehold", False),
            item.get("has_airspace", False),
            item.get("has_sea_view", False),
            item.get("is_shell_form", False),
            ready,
            key_features_en,
            key_features_en,
            key_features_pl,
            source,
            item.get("scraped_at"),
            item.get("updated_at") or now,
            distance_km,
            sea_proximity,
            url,
        )

        if existing is None:
            cursor.execute(
                """
                INSERT INTO listings (
                    title, title_en, title_pl, price_eur, locality, property_type,
                    bedrooms, area_sqm, seller_type, is_freehold, has_airspace,
                    has_sea_view, is_shell_form, ready,
                    key_features, key_features_en, key_features_pl,
                    source, scraped_at, updated_at,
                    distance_to_gzira_km, sea_proximity, url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            inserted += 1
            if item.get("price_eur") is not None:
                cursor.execute(
                    """
                    INSERT INTO price_history (url, price_eur, recorded_at)
                    VALUES (?, ?, ?)
                    """,
                    (url, item.get("price_eur"), now),
                )
        else:
            old_price = existing["price_eur"]
            new_price = item.get("price_eur")
            if new_price is not None and new_price != old_price:
                cursor.execute(
                    """
                    INSERT INTO price_history (url, price_eur, recorded_at)
                    VALUES (?, ?, ?)
                    """,
                    (url, new_price, now),
                )
                price_changes += 1

            cursor.execute(
                """
                UPDATE listings SET
                    title = ?,
                    title_en = ?,
                    title_pl = ?,
                    price_eur = ?,
                    locality = ?,
                    property_type = ?,
                    bedrooms = ?,
                    area_sqm = COALESCE(?, area_sqm),
                    seller_type = ?,
                    is_freehold = ?,
                    has_airspace = ?,
                    has_sea_view = ?,
                    is_shell_form = ?,
                    ready = ?,
                    key_features = ?,
                    key_features_en = ?,
                    key_features_pl = ?,
                    source = COALESCE(?, NULLIF(TRIM(source), '')),
                    scraped_at = COALESCE(?, scraped_at),
                    updated_at = ?,
                    distance_to_gzira_km = COALESCE(?, distance_to_gzira_km),
                    sea_proximity = COALESCE(?, sea_proximity)
                WHERE url = ?
                """,
                values,
            )
            updated += 1

    conn.commit()
    conn.close()

    stats = {
        "inserted": inserted,
        "updated": updated,
        "price_changes": price_changes,
        "skipped_gozo": skipped_gozo,
        "skipped_budget": skipped_budget,
        "skipped_hidden": skipped_hidden,
    }
    print(
        f"💾 DB '{db_name}': +{inserted} new, ~{updated} updated, "
        f"{price_changes} price change(s) logged"
        + (f", skipped {skipped_gozo} Gozo" if skipped_gozo else "")
        + (f", skipped {skipped_budget} out of budget" if skipped_budget else "")
        + (f", skipped {skipped_hidden} hidden" if skipped_hidden else "")
        + "."
    )
    return stats


def load_parsed_and_save(
    parsed_path: Path = PARSED_PATH, db_name: str | Path = DB_PATH
) -> None:
    init_db(db_name)
    removed = purge_hidden_from_json(parsed_path)
    if removed:
        print(f"🧹 Usunięto {removed} ukryte z {parsed_path.name}.")
    parsed_data = load_json_list(parsed_path)
    if not parsed_data:
        print(f"⚠️ Brak danych w {parsed_path} — nic do zapisania.")
        return
    save_listings_to_db(parsed_data, db_name=db_name)


def get_evaluated_urls(db_name: str | Path = DB_PATH) -> set[str]:
    """Return URLs that already have a stored AI evaluation."""
    if not Path(db_name).exists():
        return set()
    init_db(db_name)
    conn = _connect(db_name)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT url FROM evaluations")
        urls = {row["url"] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        urls = set()
    conn.close()
    return urls


def save_evaluation(
    url: str,
    evaluation: dict[str, Any],
    db_name: str | Path = DB_PATH,
) -> None:
    """Persist or update an LLM investment evaluation for a listing URL."""
    init_db(db_name)
    if url in get_hidden_urls(db_name):
        return
    score = evaluation["investment_score"]
    summary_en = evaluation.get("summary_en") or evaluation.get("summary", "")
    summary_pl = evaluation.get("summary_pl") or ""
    pros_en = evaluation.get("pros_en") or evaluation.get("pros", [])
    pros_pl = evaluation.get("pros_pl") or []
    cons_en = evaluation.get("cons_en") or evaluation.get("cons", [])
    cons_pl = evaluation.get("cons_pl") or []
    warnings_en = evaluation.get("buyer_warnings_en") or []
    warnings_pl = (
        evaluation.get("buyer_warnings_pl")
        or evaluation.get("buyer_warnings")
        or []
    )

    pros_en_json = _json_list_text(pros_en) or "[]"
    pros_pl_json = _json_list_text(pros_pl) or "[]"
    cons_en_json = _json_list_text(cons_en) or "[]"
    cons_pl_json = _json_list_text(cons_pl) or "[]"
    warnings_en_json = _json_list_text(warnings_en) or "[]"
    warnings_pl_json = _json_list_text(warnings_pl) or "[]"

    payload = json.dumps(evaluation, ensure_ascii=False)
    now = utc_now_iso()

    conn = _connect(db_name)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO evaluations (
            url, ai_score, ai_summary, ai_summary_en, ai_summary_pl,
            pros, pros_en, pros_pl, cons, cons_en, cons_pl,
            buyer_warnings_en, buyer_warnings_pl,
            evaluation_json, evaluated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            ai_score = excluded.ai_score,
            ai_summary = excluded.ai_summary,
            ai_summary_en = excluded.ai_summary_en,
            ai_summary_pl = excluded.ai_summary_pl,
            pros = excluded.pros,
            pros_en = excluded.pros_en,
            pros_pl = excluded.pros_pl,
            cons = excluded.cons,
            cons_en = excluded.cons_en,
            cons_pl = excluded.cons_pl,
            buyer_warnings_en = excluded.buyer_warnings_en,
            buyer_warnings_pl = excluded.buyer_warnings_pl,
            evaluation_json = excluded.evaluation_json,
            evaluated_at = excluded.evaluated_at
        """,
        (
            url,
            score,
            summary_en,
            summary_en,
            summary_pl,
            pros_en_json,
            pros_en_json,
            pros_pl_json,
            cons_en_json,
            cons_en_json,
            cons_pl_json,
            warnings_en_json,
            warnings_pl_json,
            payload,
            now,
        ),
    )
    cursor.execute(
        """
        UPDATE listings
        SET ai_score = ?, ai_summary = ?, ai_summary_en = ?, ai_summary_pl = ?,
            ai_evaluated_at = ?, area_sqm = COALESCE(?, area_sqm)
        WHERE url = ?
        """,
        (score, summary_en, summary_en, summary_pl, now, _area_from_evaluation(evaluation), url),
    )
    conn.commit()
    conn.close()
