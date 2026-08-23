"""Backfill Dhalia listing URLs to canonical /buy/{locality}/{type}/{Ref} form."""

from __future__ import annotations

import random
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from malta_housing.common import PARSED_PATH, STAGING_PATH, HttpClient, load_json_list, save_json_list
from malta_housing.paths import DB_PATH, ensure_data_dir
from malta_housing.scrapers.dhalia import (
    API_HEADERS,
    extract_ref,
    fetch_property_by_ref,
    is_canonical_url,
    listing_url,
)

_RAW_LOCALITY_RE = re.compile(r"^Locality:\s*(.+)$", re.M)
_RAW_TYPE_RE = re.compile(r"^Property type:\s*(.+)$", re.M)
_RAW_STATUS_RE = re.compile(r"^Status:\s*(.+)$", re.M)


def _delete_listing_by_url(cursor: sqlite3.Cursor, url: str) -> None:
    cursor.execute("DELETE FROM price_history WHERE url = ?", (url,))
    cursor.execute("DELETE FROM evaluations WHERE url = ?", (url,))
    cursor.execute("DELETE FROM listings WHERE url = ?", (url,))


def _rename_listing_url(cursor: sqlite3.Cursor, old_url: str, new_url: str) -> None:
    cursor.execute("UPDATE evaluations SET url = ? WHERE url = ?", (new_url, old_url))
    cursor.execute("UPDATE price_history SET url = ? WHERE url = ?", (new_url, old_url))
    cursor.execute("UPDATE listings SET url = ? WHERE url = ?", (new_url, old_url))


def _patch_raw_text(raw_text: str, old_url: str, new_url: str) -> str:
    if not raw_text or old_url == new_url:
        return raw_text
    updated = raw_text.replace(f"URL: {old_url}", f"URL: {new_url}")
    if updated == raw_text:
        updated = raw_text.replace(old_url, new_url)
    return updated


def _remove_json_item(items: list[dict[str, Any]], url: str) -> bool:
    before = len(items)
    items[:] = [
        item for item in items if not (isinstance(item, dict) and item.get("url") == url)
    ]
    return len(items) < before


def _rename_json_item(
    items: list[dict[str, Any]],
    old_url: str,
    new_url: str,
) -> bool:
    changed = False
    for item in items:
        if not isinstance(item, dict) or item.get("url") != old_url:
            continue
        item["url"] = new_url
        raw_text = item.get("raw_text")
        if isinstance(raw_text, str):
            item["raw_text"] = _patch_raw_text(raw_text, old_url, new_url)
        changed = True
    return changed


def _item_from_raw_text(ref: str, raw_text: str) -> dict[str, Any] | None:
    if not raw_text.strip():
        return None
    type_match = _RAW_TYPE_RE.search(raw_text)
    if not type_match:
        return None
    locality_match = _RAW_LOCALITY_RE.search(raw_text)
    status_match = _RAW_STATUS_RE.search(raw_text)
    return {
        "Ref": ref,
        "Location": locality_match.group(1).strip() if locality_match else "malta",
        "Type": type_match.group(1).strip(),
        "SStatus": status_match.group(1).strip() if status_match else "For Sale",
    }


def _resolve_item(
    ref: str,
    client: HttpClient,
    cache: dict[str, dict[str, Any]],
    raw_text: str = "",
) -> dict[str, Any] | None:
    item = fetch_property_by_ref(client, ref, cache=cache)
    if item:
        return item
    return _item_from_raw_text(ref, raw_text)


def _backfill_json_orphans(
    items: list[dict[str, Any]],
    client: HttpClient,
    cache: dict[str, dict[str, Any]],
    staged_by_url: dict[str, str],
    *,
    dry_run: bool,
    sleep_min: float,
    sleep_max: float,
) -> int:
    """Fix Dhalia URLs present in JSON but not tied to the DB loop above."""
    changed = 0
    seen_new: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        old_url = item.get("url") or ""
        if "dhalia.com" not in old_url.lower() and item.get("source") != "dhalia":
            continue
        if is_canonical_url(old_url):
            continue
        ref = extract_ref(old_url)
        if not ref:
            continue
        raw_text = staged_by_url.get(old_url, "")
        if not raw_text and isinstance(item.get("raw_text"), str):
            raw_text = item["raw_text"]
        api_item = _resolve_item(ref, client, cache, raw_text=raw_text)
        if not api_item:
            continue
        new_url = listing_url(api_item)
        if new_url == old_url or new_url in seen_new:
            continue
        seen_new.add(new_url)
        if dry_run:
            print(f"JSON orphan: {old_url} -> {new_url}")
            changed += 1
            continue
        item["url"] = new_url
        raw_text = item.get("raw_text")
        if isinstance(raw_text, str):
            item["raw_text"] = _patch_raw_text(raw_text, old_url, new_url)
        changed += 1
        time.sleep(random.uniform(sleep_min, sleep_max))
    return changed


def backfill_dhalia_urls(
    *,
    db_name: Path = DB_PATH,
    dry_run: bool = False,
    sleep_min: float = 0.8,
    sleep_max: float = 1.5,
) -> dict[str, int]:
    """Rewrite legacy Dhalia URLs in SQLite + staging/parsed JSON."""
    ensure_data_dir()
    conn = sqlite3.connect(str(db_name))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, url, source
        FROM listings
        WHERE source = 'dhalia' OR url LIKE '%dhalia.com%'
        ORDER BY id
        """
    )
    rows = cursor.fetchall()

    staging = load_json_list(STAGING_PATH)
    parsed = load_json_list(PARSED_PATH)
    staged_by_url = {
        item["url"]: item.get("raw_text") or ""
        for item in staging
        if isinstance(item, dict) and item.get("url")
    }
    staging_changed = False
    parsed_changed = False

    client = HttpClient(headers=API_HEADERS, timeout=30.0)
    api_cache: dict[str, dict[str, Any]] = {}
    known_urls = {row["url"] for row in cursor.execute("SELECT url FROM listings")}

    stats = {
        "total": len(rows),
        "updated": 0,
        "already_ok": 0,
        "duplicates_removed": 0,
        "no_ref": 0,
        "api_failed": 0,
        "json_staging": 0,
        "json_parsed": 0,
    }

    try:
        cursor.execute("PRAGMA foreign_keys=OFF")
        for index, row in enumerate(rows, 1):
            old_url = row["url"]
            if is_canonical_url(old_url):
                stats["already_ok"] += 1
                continue

            ref = extract_ref(old_url)
            if not ref:
                stats["no_ref"] += 1
                print(f"[{index}/{len(rows)}] Pominięto (brak ref): {old_url}")
                continue

            item = _resolve_item(
                ref,
                client,
                api_cache,
                raw_text=staged_by_url.get(old_url, ""),
            )
            if not item:
                stats["api_failed"] += 1
                print(f"[{index}/{len(rows)}] API brak danych: {ref}")
                continue

            new_url = listing_url(item)
            if new_url == old_url:
                stats["already_ok"] += 1
                continue

            if new_url in known_urls and new_url != old_url:
                print(f"[{index}/{len(rows)}] Duplikat — usuwam stary URL: {old_url}")
                if not dry_run:
                    _delete_listing_by_url(cursor, old_url)
                    if _remove_json_item(staging, old_url):
                        staging_changed = True
                        stats["json_staging"] += 1
                    if _remove_json_item(parsed, old_url):
                        parsed_changed = True
                        stats["json_parsed"] += 1
                known_urls.discard(old_url)
                stats["duplicates_removed"] += 1
                continue

            print(f"[{index}/{len(rows)}] {old_url} -> {new_url}")
            if not dry_run:
                _rename_listing_url(cursor, old_url, new_url)
                if _rename_json_item(staging, old_url, new_url):
                    staging_changed = True
                    stats["json_staging"] += 1
                if _rename_json_item(parsed, old_url, new_url):
                    parsed_changed = True
                    stats["json_parsed"] += 1

            known_urls.discard(old_url)
            known_urls.add(new_url)
            stats["updated"] += 1

            if index < len(rows):
                time.sleep(random.uniform(sleep_min, sleep_max))

        orphan_staging = _backfill_json_orphans(
            staging,
            client,
            api_cache,
            staged_by_url,
            dry_run=dry_run,
            sleep_min=sleep_min,
            sleep_max=sleep_max,
        )
        orphan_parsed = _backfill_json_orphans(
            parsed,
            client,
            api_cache,
            staged_by_url,
            dry_run=dry_run,
            sleep_min=sleep_min,
            sleep_max=sleep_max,
        )
        stats["json_staging"] += orphan_staging
        stats["json_parsed"] += orphan_parsed

        if not dry_run:
            if staging_changed or orphan_staging:
                save_json_list(STAGING_PATH, staging)
            if parsed_changed or orphan_parsed:
                save_json_list(PARSED_PATH, parsed)
            conn.commit()
    finally:
        cursor.execute("PRAGMA foreign_keys=ON")
        conn.close()

    return stats
