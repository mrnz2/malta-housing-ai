"""Budget band for mainland Malta monitoring (€100k–€400k)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from malta_housing.paths import SKIPPED_BUDGET_PATH, ensure_data_dir

MIN_PRICE_EUR = 100_000
MAX_PRICE_EUR = 400_000

EUR_AMOUNT_RE = re.compile(r"€\s*([\d,]+)")
PRICE_HEADER_RE = re.compile(r"^Price:\s*(.+)$", re.M | re.I)
_PER_SQM_HINT_RE = re.compile(r"/\s*m\s*[²2]|per\s+sq", re.I)

# Ignore small € amounts in fallback scan (likely €/m² or fees, not listing price).
_FALLBACK_MIN_AMOUNT = MIN_PRICE_EUR // 2
_FALLBACK_SCAN_CHARS = 2_500


def parse_eur_amount(text: str) -> int | None:
    """Parse the first € amount from text, e.g. '€ 650,000' -> 650000."""
    match = EUR_AMOUNT_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def price_eur_from_raw_text(raw_text: str) -> int | None:
    """Read listing price from an explicit 'Price: …' line in staged raw_text."""
    for line in raw_text.splitlines():
        match = PRICE_HEADER_RE.match(line.strip())
        if not match:
            continue
        value = match.group(1).strip()
        if not value or value.lower() in {"n/a", "na", "none", "-"}:
            continue
        price = parse_eur_amount(value)
        if price is not None:
            return price
    return None


def _fallback_price_eur_from_raw_text(raw_text: str) -> int | None:
    """Best-effort listing price from early raw_text when no Price: header exists."""
    header = raw_text[:_FALLBACK_SCAN_CHARS]
    for match in EUR_AMOUNT_RE.finditer(header):
        start = max(0, match.start() - 24)
        end = min(len(header), match.end() + 24)
        context = header[start:end]
        if _PER_SQM_HINT_RE.search(context):
            continue
        try:
            amount = int(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if amount >= _FALLBACK_MIN_AMOUNT:
            return amount
    return None


def price_eur_from_staged(item: dict[str, Any]) -> int | None:
    """Extract listing price from a staged scrape before LLM parsing."""
    price = item.get("price_eur")
    if price is not None and not isinstance(price, bool) and isinstance(price, (int, float)):
        return int(price)

    raw_text = item.get("raw_text") or ""
    price = price_eur_from_raw_text(raw_text)
    if price is not None:
        return price
    return _fallback_price_eur_from_raw_text(raw_text)


def is_out_of_budget(
    item: dict[str, Any],
    *,
    min_price_eur: int = MIN_PRICE_EUR,
    max_price_eur: int = MAX_PRICE_EUR,
) -> bool:
    """True when price_eur is a known number outside the budget band.

    Missing / null price is not out of budget (same as purge: keep unknowns).
    """
    price = item.get("price_eur")
    if price is None:
        return False
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        return False
    return price < min_price_eur or price > max_price_eur


def is_staged_out_of_budget(item: dict[str, Any]) -> bool:
    """True when staged raw_text exposes a known price outside the budget band."""
    price = price_eur_from_staged(item)
    if price is None:
        return False
    return is_out_of_budget({"price_eur": price})


def load_skipped_budget_urls(path: Path = SKIPPED_BUDGET_PATH) -> set[str]:
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return set()
    return {str(url) for url in data if url}


def remember_skipped_budget_url(url: str, path: Path = SKIPPED_BUDGET_PATH) -> None:
    if not url:
        return
    urls = load_skipped_budget_urls(path)
    if url in urls:
        return
    urls.add(url)
    ensure_data_dir()
    with path.open("w", encoding="utf-8") as f:
        json.dump(sorted(urls), f, indent=2, ensure_ascii=False)
