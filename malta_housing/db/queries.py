"""Read-only query helpers for the local browser UI."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from malta_housing.db.store import _connect
from malta_housing.paths import DB_PATH

_EMPTY_STATS: dict[str, Any] = {
    "total": 0,
    "with_price": 0,
    "avg_price": None,
    "min_price": None,
    "max_price": None,
    "with_score": 0,
    "avg_ai_score": None,
    "sources": {},
    "localities": [],
    "seller_types": [],
    "property_types": [],
}

# Earliest calendar day from scraped_at / created_at (matches _first_seen_day).
_FIRST_SEEN_DAY_SQL = """
CASE
  WHEN scraped_at IS NOT NULL AND created_at IS NOT NULL THEN
    CASE
      WHEN substr(scraped_at, 1, 10) < substr(created_at, 1, 10)
      THEN substr(scraped_at, 1, 10)
      ELSE substr(created_at, 1, 10)
    END
  WHEN scraped_at IS NOT NULL THEN substr(scraped_at, 1, 10)
  WHEN created_at IS NOT NULL THEN substr(created_at, 1, 10)
  ELSE NULL
END
""".strip()

# New listings first (UTC today), then by score within each group.
_NEW_FIRST_SQL = f"CASE WHEN ({_FIRST_SEEN_DAY_SQL}) = date('now') THEN 0 ELSE 1 END"

_ORDER_SQL = {
    "created_desc": "CASE WHEN created_at IS NULL THEN 1 ELSE 0 END, created_at DESC, id DESC",
    "created_asc": "CASE WHEN created_at IS NULL THEN 1 ELSE 0 END, created_at ASC, id ASC",
    "updated_desc": "CASE WHEN updated_at IS NULL THEN 1 ELSE 0 END, updated_at DESC, id DESC",
    "updated_asc": "CASE WHEN updated_at IS NULL THEN 1 ELSE 0 END, updated_at ASC, id ASC",
    "price_asc": "CASE WHEN price_eur IS NULL THEN 1 ELSE 0 END, price_eur ASC, id DESC",
    "price_desc": "CASE WHEN price_eur IS NULL THEN 1 ELSE 0 END, price_eur DESC, id DESC",
    "locality_asc": "CASE WHEN locality IS NULL THEN 1 ELSE 0 END, locality COLLATE NOCASE ASC, id DESC",
    "title_asc": "title COLLATE NOCASE ASC, id DESC",
    "gzira_asc": "CASE WHEN distance_to_gzira_km IS NULL THEN 1 ELSE 0 END, distance_to_gzira_km ASC, id DESC",
    "gzira_desc": "CASE WHEN distance_to_gzira_km IS NULL THEN 1 ELSE 0 END, distance_to_gzira_km DESC, id DESC",
    "ai_score_desc": (
        f"{_NEW_FIRST_SQL}, CASE WHEN ai_score IS NULL THEN 1 ELSE 0 END, "
        "ai_score DESC, id DESC"
    ),
    "ai_score_asc": (
        f"{_NEW_FIRST_SQL}, CASE WHEN ai_score IS NULL THEN 1 ELSE 0 END, "
        "ai_score ASC, id DESC"
    ),
}

_LISTING_COLUMNS = """
    id, url, title, price_eur, locality, property_type, bedrooms,
    seller_type, is_freehold, has_airspace, has_sea_view, is_shell_form,
    key_features, source, scraped_at, created_at, updated_at,
    distance_to_gzira_km, is_hidden, notes,
    ai_score, ai_summary, ai_evaluated_at, sea_proximity
"""

_LISTING_COLUMNS_QUALIFIED = """
    listings.id, listings.url, listings.title, listings.price_eur, listings.locality,
    listings.property_type, listings.bedrooms, listings.seller_type, listings.is_freehold,
    listings.has_airspace, listings.has_sea_view, listings.is_shell_form,
    listings.key_features, listings.source, listings.scraped_at, listings.created_at,
    listings.updated_at, listings.distance_to_gzira_km, listings.is_hidden, listings.notes,
    listings.ai_score, listings.ai_summary, listings.ai_evaluated_at, listings.sea_proximity
"""


def _row_to_listing(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    features = data.get("key_features")
    if isinstance(features, str):
        try:
            data["key_features"] = json.loads(features)
        except json.JSONDecodeError:
            data["key_features"] = []
    elif features is None:
        data["key_features"] = []
    for flag in ("is_freehold", "has_airspace", "has_sea_view", "is_shell_form", "is_hidden"):
        if flag in data and data[flag] is not None:
            data[flag] = bool(data[flag])
        elif flag == "is_hidden":
            data[flag] = False
    return data


def _latest_scrape_day(cur: sqlite3.Cursor) -> str | None:
    cur.execute("SELECT MAX(scraped_at) AS mx FROM listings WHERE scraped_at IS NOT NULL")
    mx = cur.fetchone()["mx"]
    return str(mx)[:10] if mx else None


def get_latest_scrape_day(db_name: str | Path = DB_PATH) -> str | None:
    if not Path(db_name).exists():
        return None
    conn = _connect(db_name)
    cur = conn.cursor()
    try:
        return _latest_scrape_day(cur)
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def _utc_today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _date_prefix(value: Any) -> str | None:
    if not value:
        return None
    return str(value)[:10]


def _first_seen_day(item: dict[str, Any]) -> str | None:
    """Earliest calendar day the listing was scraped or first persisted."""
    days = [_date_prefix(item.get(key)) for key in ("scraped_at", "created_at")]
    days = [day for day in days if day]
    return min(days) if days else None


def _annotate_is_new(items: list[dict[str, Any]]) -> None:
    today = _utc_today_str()
    for item in items:
        first_day = _first_seen_day(item)
        item["is_new"] = first_day == today


_VISIBLE = "(is_hidden = 0 OR is_hidden IS NULL)"
_HIDDEN = "is_hidden = 1"


def get_stats(db_name: str | Path = DB_PATH) -> dict[str, Any]:
    if not Path(db_name).exists():
        return dict(_EMPTY_STATS)

    conn = _connect(db_name)
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) AS n FROM listings WHERE {_VISIBLE}")
        total = cur.fetchone()["n"]

        cur.execute(
            f"""
            SELECT
                COUNT(price_eur) AS with_price,
                AVG(price_eur) AS avg_price,
                MIN(price_eur) AS min_price,
                MAX(price_eur) AS max_price
            FROM listings
            WHERE price_eur IS NOT NULL AND {_VISIBLE}
            """
        )
        price_row = cur.fetchone()

        cur.execute(
            f"""
            SELECT COALESCE(source, 'unknown') AS source, COUNT(*) AS n
            FROM listings
            WHERE {_VISIBLE}
            GROUP BY COALESCE(source, 'unknown')
            ORDER BY n DESC
            """
        )
        sources = {row["source"]: row["n"] for row in cur.fetchall()}

        cur.execute(
            f"""
            SELECT locality FROM listings
            WHERE locality IS NOT NULL AND TRIM(locality) != '' AND {_VISIBLE}
            GROUP BY locality
            ORDER BY locality COLLATE NOCASE
            """
        )
        localities = [row["locality"] for row in cur.fetchall()]

        cur.execute(
            f"""
            SELECT seller_type FROM listings
            WHERE seller_type IS NOT NULL AND TRIM(seller_type) != '' AND {_VISIBLE}
            GROUP BY seller_type
            ORDER BY seller_type
            """
        )
        seller_types = [row["seller_type"] for row in cur.fetchall()]

        cur.execute(
            f"""
            SELECT property_type FROM listings
            WHERE property_type IS NOT NULL AND TRIM(property_type) != '' AND {_VISIBLE}
            GROUP BY property_type
            ORDER BY property_type COLLATE NOCASE
            """
        )
        property_types = [row["property_type"] for row in cur.fetchall()]

        cur.execute(
            f"""
            SELECT
                COUNT(ai_score) AS with_score,
                AVG(ai_score) AS avg_ai_score
            FROM listings
            WHERE ai_score IS NOT NULL AND {_VISIBLE}
            """
        )
        score_row = cur.fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return dict(_EMPTY_STATS)

    conn.close()
    return {
        "total": total,
        "with_price": price_row["with_price"] or 0,
        "avg_price": int(price_row["avg_price"]) if price_row["avg_price"] is not None else None,
        "min_price": price_row["min_price"],
        "max_price": price_row["max_price"],
        "with_score": score_row["with_score"] or 0,
        "avg_ai_score": (
            round(float(score_row["avg_ai_score"]), 1)
            if score_row["avg_ai_score"] is not None
            else None
        ),
        "sources": sources,
        "localities": localities,
        "seller_types": seller_types,
        "property_types": property_types,
    }


def list_listings(
    *,
    q: str | None = None,
    locality: str | None = None,
    source: str | None = None,
    seller_type: str | None = None,
    property_type: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    freehold: bool | None = None,
    airspace: bool | None = None,
    show_hidden: bool = False,
    sort: str = "created_desc",
    limit: int = 100,
    offset: int = 0,
    db_name: str | Path = DB_PATH,
) -> dict[str, Any]:
    if not Path(db_name).exists():
        return {"total": 0, "items": [], "limit": limit, "offset": offset}

    where: list[str] = []
    params: list[Any] = []

    if show_hidden:
        where.append(_HIDDEN)
    else:
        where.append(_VISIBLE)

    if q:
        where.append("(title LIKE ? OR locality LIKE ? OR url LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if locality:
        where.append("locality = ?")
        params.append(locality)
    if source:
        where.append("source = ?")
        params.append(source)
    if seller_type:
        where.append("seller_type = ?")
        params.append(seller_type)
    if property_type:
        where.append("property_type = ?")
        params.append(property_type)
    if min_price is not None:
        where.append("price_eur >= ?")
        params.append(min_price)
    if max_price is not None:
        where.append("price_eur <= ?")
        params.append(max_price)
    if freehold is True:
        where.append("is_freehold = 1")
    if airspace is True:
        where.append("has_airspace = 1")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_sql = _ORDER_SQL.get(sort, _ORDER_SQL["created_desc"])
    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    conn = _connect(db_name)
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) AS n FROM listings {where_sql}", params)
        total = cur.fetchone()["n"]

        cur.execute(
            f"""
            SELECT
                {_LISTING_COLUMNS}
            FROM listings
            {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        )
        items = [_row_to_listing(row) for row in cur.fetchall()]
        _annotate_is_new(items)
    except sqlite3.OperationalError:
        conn.close()
        return {"total": 0, "items": [], "limit": limit, "offset": offset}

    conn.close()
    return {"total": total, "items": items, "limit": limit, "offset": offset}


def get_listing(listing_id: int, db_name: str | Path = DB_PATH) -> dict[str, Any] | None:
    if not Path(db_name).exists():
        return None
    conn = _connect(db_name)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT
                {_LISTING_COLUMNS_QUALIFIED},
                e.pros, e.cons, e.evaluation_json
            FROM listings
            LEFT JOIN evaluations e ON e.url = listings.url
            WHERE listings.id = ?
            """,
            (listing_id,),
        )
        row = cur.fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return None
    conn.close()
    if not row:
        return None
    listing = _enrich_with_evaluation(_row_to_listing(row))
    _annotate_is_new([listing])
    return listing


def get_rank_candidates(
    *,
    max_price: int | None = None,
    source: str | None = None,
    db_name: str | Path = DB_PATH,
) -> list[dict[str, Any]]:
    """Visible listings eligible for investment ranking (optionally price-capped)."""
    if not Path(db_name).exists():
        return []

    where = [_VISIBLE]
    params: list[Any] = []
    if max_price is not None:
        where.append("price_eur IS NOT NULL AND price_eur <= ?")
        params.append(max_price)
    if source is not None:
        where.append("source = ?")
        params.append(source)

    where_sql = f"WHERE {' AND '.join(where)}"
    conn = _connect(db_name)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT
                {_LISTING_COLUMNS}
            FROM listings
            {where_sql}
            ORDER BY price_eur ASC, id DESC
            """,
            params,
        )
        items = [_row_to_listing(row) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        items = []
    conn.close()
    return items


def _enrich_with_evaluation(row: dict[str, Any]) -> dict[str, Any]:
    pros = row.get("pros")
    cons = row.get("cons")
    if isinstance(pros, str):
        try:
            row["pros"] = json.loads(pros)
        except json.JSONDecodeError:
            row["pros"] = []
    if isinstance(cons, str):
        try:
            row["cons"] = json.loads(cons)
        except json.JSONDecodeError:
            row["cons"] = []

    payload = row.get("evaluation_json")
    if isinstance(payload, str) and payload.strip():
        try:
            evaluation = json.loads(payload)
        except json.JSONDecodeError:
            evaluation = None
        if isinstance(evaluation, dict):
            for key in (
                "base_score",
                "qualitative_adjustment",
                "score_breakdown",
                "metrics",
            ):
                if key in evaluation and key not in row:
                    row[key] = evaluation[key]
    row.pop("evaluation_json", None)
    return row


def get_ranked_listings(
    *,
    max_price: int | None = None,
    limit: int = 100,
    db_name: str | Path = DB_PATH,
) -> list[dict[str, Any]]:
    """Listings joined with evaluations, sorted by ai_score descending."""
    if not Path(db_name).exists():
        return []

    where = [_VISIBLE.replace("is_hidden", "listings.is_hidden")]
    params: list[Any] = []
    if max_price is not None:
        where.append("listings.price_eur IS NOT NULL AND listings.price_eur <= ?")
        params.append(max_price)
    where.append("listings.ai_score IS NOT NULL")

    where_sql = f"WHERE {' AND '.join(where)}"
    limit = max(1, min(limit, 500))

    conn = _connect(db_name)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT
                {_LISTING_COLUMNS_QUALIFIED},
                e.pros, e.cons, e.evaluation_json, e.evaluated_at
            FROM listings
            LEFT JOIN evaluations e ON e.url = listings.url
            {where_sql}
            ORDER BY listings.ai_score DESC, listings.price_eur ASC, listings.id DESC
            LIMIT ?
            """,
            [*params, limit],
        )
        items = [_enrich_with_evaluation(_row_to_listing(row)) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        items = []
    conn.close()
    return items


def get_price_history(listing_id: int, db_name: str | Path = DB_PATH) -> list[dict[str, Any]]:
    listing = get_listing(listing_id, db_name=db_name)
    if not listing:
        return []
    conn = _connect(db_name)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, url, price_eur, recorded_at
            FROM price_history
            WHERE url = ?
            ORDER BY recorded_at ASC, id ASC
            """,
            (listing["url"],),
        )
        history = [dict(row) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        history = []
    conn.close()
    return history
