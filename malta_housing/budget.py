"""Budget band for mainland Malta monitoring (€100k–€400k)."""

from __future__ import annotations

from typing import Any

MIN_PRICE_EUR = 100_000
MAX_PRICE_EUR = 400_000


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
