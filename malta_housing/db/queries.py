"""Read-only query helpers for the local browser UI."""

from __future__ import annotations

import json
import sqlite3
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
    "sources": {},
    "localities": [],
    "seller_types": [],
    "property_types": [],
}

_ORDER_SQL = {
    "updated_desc": "CASE WHEN updated_at IS NULL THEN 1 ELSE 0 END, updated_at DESC, id DESC",
    "updated_asc": "CASE WHEN updated_at IS NULL THEN 1 ELSE 0 END, updated_at ASC, id ASC",
    "price_asc": "CASE WHEN price_eur IS NULL THEN 1 ELSE 0 END, price_eur ASC, id DESC",
    "price_desc": "CASE WHEN price_eur IS NULL THEN 1 ELSE 0 END, price_eur DESC, id DESC",
    "locality_asc": "CASE WHEN locality IS NULL THEN 1 ELSE 0 END, locality COLLATE NOCASE ASC, id DESC",
    "title_asc": "title COLLATE NOCASE ASC, id DESC",
    "gzira_asc": "CASE WHEN distance_to_gzira_km IS NULL THEN 1 ELSE 0 END, distance_to_gzira_km ASC, id DESC",
    "gzira_desc": "CASE WHEN distance_to_gzira_km IS NULL THEN 1 ELSE 0 END, distance_to_gzira_km DESC, id DESC",
}


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
    for flag in ("is_freehold", "has_airspace", "has_sea_view", "is_shell_form"):
        if flag in data and data[flag] is not None:
            data[flag] = bool(data[flag])
    return data


def get_stats(db_name: str | Path = DB_PATH) -> dict[str, Any]:
    if not Path(db_name).exists():
        return dict(_EMPTY_STATS)

    conn = _connect(db_name)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) AS n FROM listings")
        total = cur.fetchone()["n"]

        cur.execute(
            """
            SELECT
                COUNT(price_eur) AS with_price,
                AVG(price_eur) AS avg_price,
                MIN(price_eur) AS min_price,
                MAX(price_eur) AS max_price
            FROM listings
            WHERE price_eur IS NOT NULL
            """
        )
        price_row = cur.fetchone()

        cur.execute(
            """
            SELECT COALESCE(source, 'unknown') AS source, COUNT(*) AS n
            FROM listings
            GROUP BY COALESCE(source, 'unknown')
            ORDER BY n DESC
            """
        )
        sources = {row["source"]: row["n"] for row in cur.fetchall()}

        cur.execute(
            """
            SELECT locality FROM listings
            WHERE locality IS NOT NULL AND TRIM(locality) != ''
            GROUP BY locality
            ORDER BY locality COLLATE NOCASE
            """
        )
        localities = [row["locality"] for row in cur.fetchall()]

        cur.execute(
            """
            SELECT seller_type FROM listings
            WHERE seller_type IS NOT NULL AND TRIM(seller_type) != ''
            GROUP BY seller_type
            ORDER BY seller_type
            """
        )
        seller_types = [row["seller_type"] for row in cur.fetchall()]

        cur.execute(
            """
            SELECT property_type FROM listings
            WHERE property_type IS NOT NULL AND TRIM(property_type) != ''
            GROUP BY property_type
            ORDER BY property_type COLLATE NOCASE
            """
        )
        property_types = [row["property_type"] for row in cur.fetchall()]
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
    sort: str = "updated_desc",
    limit: int = 100,
    offset: int = 0,
    db_name: str | Path = DB_PATH,
) -> dict[str, Any]:
    if not Path(db_name).exists():
        return {"total": 0, "items": [], "limit": limit, "offset": offset}

    where: list[str] = []
    params: list[Any] = []

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
    order_sql = _ORDER_SQL.get(sort, _ORDER_SQL["updated_desc"])
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
                id, url, title, price_eur, locality, property_type, bedrooms,
                seller_type, is_freehold, has_airspace, has_sea_view, is_shell_form,
                key_features, source, scraped_at, created_at, updated_at,
                distance_to_gzira_km
            FROM listings
            {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        )
        items = [_row_to_listing(row) for row in cur.fetchall()]
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
            """
            SELECT
                id, url, title, price_eur, locality, property_type, bedrooms,
                seller_type, is_freehold, has_airspace, has_sea_view, is_shell_form,
                key_features, source, scraped_at, created_at, updated_at,
                distance_to_gzira_km
            FROM listings
            WHERE id = ?
            """,
            (listing_id,),
        )
        row = cur.fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return None
    conn.close()
    return _row_to_listing(row) if row else None


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
