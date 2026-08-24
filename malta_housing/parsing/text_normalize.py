"""Normalize ALL-CAPS listing text: lowercase except sentence starts and proper names."""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

from malta_housing.distances import GZIRA_CSV_PATH
from malta_housing.geo import _normalize
from malta_housing.paths import DB_PATH

_WORD_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)
_SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)|\n")

_EXTRA_PROPER_NAMES: dict[str, str] = {
    "malta": "Malta",
    "gozo": "Gozo",
    "eu": "EU",
    "eur": "EUR",
    "remax": "ReMax",
    "re max": "ReMax",
}

# Spelling variants only — not geographic sub-localities (see distances._ALIASES).
_LOCALITY_SPELLING_ALIASES: dict[str, str] = {
    "birzebbuga": "birzebbugia",
}


def _display_forms_for_locality(cell: str) -> list[str]:
    raw = cell.strip()
    if not raw:
        return []
    forms = [raw]
    if "(" in raw and ")" in raw:
        before = raw.split("(", 1)[0].strip()
        inside = raw[raw.find("(") + 1 : raw.rfind(")")].strip()
        if before:
            forms.append(before)
        if inside:
            forms.append(inside)
    return forms


def _phrase_key(words: list[str]) -> str:
    return _normalize(" ".join(words))


def _phrase_keys(words: list[str]) -> set[str]:
    keys = {_phrase_key(words)}
    collapsed = [word.replace("'", "") for word in words]
    if collapsed != list(words):
        keys.add(_phrase_key(collapsed))
    return keys


@lru_cache(maxsize=1)
def _proper_name_maps() -> tuple[dict[str, str], dict[str, str]]:
    """Return (single_word_map, multi_word_phrase_map) normalized key → display form."""
    single: dict[str, str] = dict(_EXTRA_PROPER_NAMES)
    phrases: dict[str, str] = {}

    path = Path(GZIRA_CSV_PATH)
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cell = (row.get("Miejscowość") or row.get("locality") or "").strip()
                for form in _display_forms_for_locality(cell):
                    words = _WORD_RE.findall(form)
                    if not words:
                        continue
                    if len(words) == 1:
                        single[_normalize(words[0])] = words[0]
                    else:
                        for key in _phrase_keys(words):
                            phrases[key] = form

    for key, display in list(single.items()):
        if " " in display:
            phrases.setdefault(key, display)

    return single, phrases


def _is_all_caps_word(word: str) -> bool:
    letters = [ch for ch in word if ch.isalpha()]
    if not letters:
        return False
    return all(ch.isupper() for ch in letters)


def _capitalize_word(word: str) -> str:
    if not word:
        return word
    if len(word) == 1:
        return word.upper()
    return word[0].upper() + word[1:].lower()


def _normalize_word(word: str, *, capitalize: bool, single_map: dict[str, str]) -> str:
    key = _normalize(word)
    if key in single_map:
        return single_map[key]

    if not _is_all_caps_word(word):
        return word

    lowered = word.lower()
    if capitalize:
        return _capitalize_word(lowered)
    return lowered


def normalize_display_text(text: str) -> str:
    """Lowercase ALL-CAPS words; keep sentence starts capitalized and proper names."""
    if not text or not text.strip():
        return text

    single_map, phrase_map = _proper_name_maps()
    max_phrase_words = max((len(_WORD_RE.findall(k)) for k in phrase_map), default=1)

    out: list[str] = []
    sentence_start = True
    first_alpha = True
    parts = _split_words_and_separators(text)
    word_indices = [i for i, (kind, _) in enumerate(parts) if kind == "word"]
    word_cursor = 0

    i = 0
    while i < len(parts):
        kind, value = parts[i]
        if kind == "sep":
            if _SENTENCE_END_RE.search(value):
                sentence_start = True
            out.append(value)
            i += 1
            continue

        remaining_words = len(word_indices) - word_cursor
        matched_phrase: str | None = None
        matched_len = 0
        for size in range(min(max_phrase_words, remaining_words), 1, -1):
            chunk_words = [parts[j][1] for j in word_indices[word_cursor : word_cursor + size]]
            matched = False
            for key in _phrase_keys(chunk_words):
                if key in phrase_map:
                    matched_phrase = phrase_map[key]
                    matched_len = size
                    matched = True
                    break
            if matched:
                break

        if matched_phrase is not None:
            out.append(matched_phrase)
            word_cursor += matched_len
            i = word_indices[word_cursor - 1] + 1
            sentence_start = False
            first_alpha = False
            continue

        capitalize = sentence_start or first_alpha
        out.append(_normalize_word(value, capitalize=capitalize, single_map=single_map))
        sentence_start = False
        first_alpha = False
        word_cursor += 1
        i += 1

    return "".join(out)


def _display_for_key(
    key: str,
    single_map: dict[str, str],
    phrase_map: dict[str, str],
) -> str | None:
    if key in phrase_map:
        return phrase_map[key]
    if key in single_map:
        return single_map[key]
    alias = _LOCALITY_SPELLING_ALIASES.get(key)
    if alias:
        if alias in phrase_map:
            return phrase_map[alias]
        if alias in single_map:
            return single_map[alias]
    return None


def normalize_locality_text(locality: str) -> str:
    """Normalize casing and map to a known Malta locality display form when possible."""
    text = locality.strip()
    if not text:
        return text

    single_map, phrase_map = _proper_name_maps()
    max_phrase_words = max((len(_WORD_RE.findall(k)) for k in phrase_map), default=1)
    words = _WORD_RE.findall(text)
    if words:
        for size in range(min(len(words), max_phrase_words), 0, -1):
            for key in _phrase_keys(words[:size]):
                display = _display_for_key(key, single_map, phrase_map)
                if display:
                    return display
        if len(words) == 1:
            key = _normalize(words[0])
            display = _display_for_key(key, single_map, phrase_map)
            if display:
                return display

    return normalize_display_text(text)


def _split_words_and_separators(text: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    last = 0
    for match in _WORD_RE.finditer(text):
        if match.start() > last:
            parts.append(("sep", text[last:match.start()]))
        parts.append(("word", match.group(0)))
        last = match.end()
    if last < len(text):
        parts.append(("sep", text[last:]))
    return parts


def _normalize_title_value(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    return normalize_display_text(str(value).strip())


def run_normalize_titles(db_name: str | Path = DB_PATH) -> dict[str, int]:
    """Normalize title casing in SQLite and parsed_listings.json (visible listings only)."""
    from malta_housing.common import PARSED_PATH, load_json_list, save_json_list
    from malta_housing.db.store import _connect, get_hidden_urls, init_db

    db_path = Path(db_name)
    init_db(db_path)
    hidden = get_hidden_urls(db_path)

    conn = _connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, url, title, title_en, title_pl
        FROM listings
        WHERE (is_hidden = 0 OR is_hidden IS NULL)
        """
    )
    rows = [dict(row) for row in cur.fetchall()]

    db_updated = 0
    for row in rows:
        if row["url"] in hidden:
            continue

        current_en = (row.get("title_en") or row.get("title") or "").strip()
        current_pl = (row.get("title_pl") or "").strip()
        new_en = _normalize_title_value(current_en) if current_en else None
        new_pl = _normalize_title_value(current_pl) if current_pl else None

        title_en_out = new_en if new_en is not None else (row.get("title_en") or None)
        title_pl_out = new_pl if new_pl is not None else (row.get("title_pl") or None)
        title_out = title_en_out or row.get("title")

        changed = (
            (new_en is not None and new_en != current_en)
            or (new_pl is not None and new_pl != current_pl)
            or (title_out and title_out != (row.get("title") or "").strip())
        )
        if not changed:
            continue

        cur.execute(
            """
            UPDATE listings
            SET title = ?, title_en = ?, title_pl = ?
            WHERE id = ?
            """,
            (title_out, title_en_out, title_pl_out, row["id"]),
        )
        db_updated += 1

    conn.commit()
    conn.close()

    parsed_updated = 0
    parsed_items = load_json_list(PARSED_PATH)
    if parsed_items:
        for item in parsed_items:
            url = item.get("url")
            if not url or url in hidden:
                continue

            current_en = (item.get("title_en") or item.get("title") or "").strip()
            current_pl = (item.get("title_pl") or "").strip()
            new_en = _normalize_title_value(current_en) if current_en else None
            new_pl = _normalize_title_value(current_pl) if current_pl else None

            changed = False
            if new_en is not None and new_en != current_en:
                item["title_en"] = new_en
                item["title"] = new_en
                changed = True
            if new_pl is not None and new_pl != current_pl:
                item["title_pl"] = new_pl
                changed = True
            if changed:
                parsed_updated += 1

        if parsed_updated:
            save_json_list(PARSED_PATH, parsed_items)

    print(
        f"✅ Title normalization done: {db_updated} updated in DB, "
        f"{parsed_updated} updated in {PARSED_PATH.name}, "
        f"{len(rows)} visible listing(s) scanned."
    )
    return {
        "db_updated": db_updated,
        "parsed_updated": parsed_updated,
        "scanned": len(rows),
    }


def run_normalize_localities(db_name: str | Path = DB_PATH) -> dict[str, int]:
    """Normalize locality spellings in SQLite and parsed_listings.json."""
    from malta_housing.common import PARSED_PATH, load_json_list, save_json_list
    from malta_housing.db.store import _connect, init_db
    from malta_housing.distances import distance_to_gzira_km, sea_proximity_for

    db_path = Path(db_name)
    init_db(db_path)

    conn = _connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, locality FROM listings
        WHERE locality IS NOT NULL AND TRIM(locality) != ''
        """
    )
    rows = [dict(row) for row in cur.fetchall()]

    db_updated = 0
    for row in rows:
        current = (row.get("locality") or "").strip()
        new_loc = normalize_locality_text(current)
        if not new_loc or new_loc == current:
            continue
        km = distance_to_gzira_km(new_loc)
        sea = sea_proximity_for(new_loc)
        cur.execute(
            """
            UPDATE listings
            SET locality = ?,
                distance_to_gzira_km = COALESCE(?, distance_to_gzira_km),
                sea_proximity = COALESCE(?, sea_proximity)
            WHERE id = ?
            """,
            (new_loc, km, sea, row["id"]),
        )
        db_updated += 1

    conn.commit()
    conn.close()

    parsed_updated = 0
    parsed_items = load_json_list(PARSED_PATH)
    if parsed_items:
        for item in parsed_items:
            current = (item.get("locality") or "").strip()
            if not current:
                continue
            new_loc = normalize_locality_text(current)
            if new_loc and new_loc != current:
                item["locality"] = new_loc
                parsed_updated += 1

        if parsed_updated:
            save_json_list(PARSED_PATH, parsed_items)

    print(
        f"✅ Locality normalization done: {db_updated} updated in DB, "
        f"{parsed_updated} updated in {PARSED_PATH.name}, "
        f"{len(rows)} listing(s) scanned."
    )
    return {
        "db_updated": db_updated,
        "parsed_updated": parsed_updated,
        "scanned": len(rows),
    }
