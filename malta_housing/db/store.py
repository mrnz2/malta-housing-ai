"""SQLite persistence with UPSERT and price history tracking."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from malta_housing.budget import MAX_PRICE_EUR, MIN_PRICE_EUR, is_out_of_budget
from malta_housing.common import PARSED_PATH, load_json_list, resolve_source
from malta_housing.distances import distance_to_gzira_km, sea_proximity_for
from malta_housing.geo import is_gozo_record
from malta_housing.models import utc_now_iso
from malta_housing.paths import DB_PATH, ensure_data_dir


def _connect(db_name: str | Path = DB_PATH) -> sqlite3.Connection:
    ensure_data_dir()
    conn = sqlite3.connect(str(db_name))
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

    conn.commit()
    _backfill_locality_fields(conn)
    _backfill_sources(conn)
    _backfill_listing_scores(conn)
    conn.close()


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
    now = utc_now_iso()

    for item in listings:
        if is_gozo_record(item):
            skipped_gozo += 1
            continue
        if is_out_of_budget(item):
            skipped_budget += 1
            continue

        url = item["url"]
        key_features = item.get("key_features", [])
        if not isinstance(key_features, str):
            key_features = json.dumps(key_features, ensure_ascii=False)

        distance_km = item.get("distance_to_gzira_km")
        if distance_km is None:
            distance_km = distance_to_gzira_km(item.get("locality"))

        sea_proximity = item.get("sea_proximity")
        if not sea_proximity:
            sea_proximity = sea_proximity_for(item.get("locality"))

        source = resolve_source(item.get("source"), url)

        cursor.execute("SELECT price_eur, title FROM listings WHERE url = ?", (url,))
        existing = cursor.fetchone()

        ready = item.get("ready")
        if ready is not None:
            ready = bool(ready)

        values = (
            item.get("title"),
            item.get("price_eur"),
            item.get("locality"),
            item.get("property_type"),
            item.get("bedrooms"),
            item.get("seller_type"),
            item.get("is_freehold", False),
            item.get("has_airspace", False),
            item.get("has_sea_view", False),
            item.get("is_shell_form", False),
            ready,
            key_features,
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
                    title, price_eur, locality, property_type,
                    bedrooms, seller_type, is_freehold, has_airspace,
                    has_sea_view, is_shell_form, ready, key_features, source,
                    scraped_at, updated_at, distance_to_gzira_km, sea_proximity, url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    price_eur = ?,
                    locality = ?,
                    property_type = ?,
                    bedrooms = ?,
                    seller_type = ?,
                    is_freehold = ?,
                    has_airspace = ?,
                    has_sea_view = ?,
                    is_shell_form = ?,
                    ready = ?,
                    key_features = ?,
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
    }
    print(
        f"💾 DB '{db_name}': +{inserted} new, ~{updated} updated, "
        f"{price_changes} price change(s) logged"
        + (f", skipped {skipped_gozo} Gozo" if skipped_gozo else "")
        + (f", skipped {skipped_budget} out of budget" if skipped_budget else "")
        + "."
    )
    return stats


def load_parsed_and_save(
    parsed_path: Path = PARSED_PATH, db_name: str | Path = DB_PATH
) -> None:
    init_db(db_name)
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
    score = evaluation["investment_score"]
    summary = evaluation.get("summary", "")
    pros = evaluation.get("pros", [])
    cons = evaluation.get("cons", [])
    if not isinstance(pros, str):
        pros = json.dumps(pros, ensure_ascii=False)
    if not isinstance(cons, str):
        cons = json.dumps(cons, ensure_ascii=False)
    payload = json.dumps(evaluation, ensure_ascii=False)
    now = utc_now_iso()

    conn = _connect(db_name)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO evaluations (url, ai_score, ai_summary, pros, cons, evaluation_json, evaluated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            ai_score = excluded.ai_score,
            ai_summary = excluded.ai_summary,
            pros = excluded.pros,
            cons = excluded.cons,
            evaluation_json = excluded.evaluation_json,
            evaluated_at = excluded.evaluated_at
        """,
        (url, score, summary, pros, cons, payload, now),
    )
    cursor.execute(
        """
        UPDATE listings
        SET ai_score = ?, ai_summary = ?, ai_evaluated_at = ?
        WHERE url = ?
        """,
        (score, summary, now, url),
    )
    conn.commit()
    conn.close()
