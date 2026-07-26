"""Geographic filters for Malta mainland vs Gozo."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Localities that exist only (or primarily) on Gozo — match without requiring "Gozo" in text.
_GOZO_ONLY_LOCALITIES = frozenset(
    {
        "gharb",
        "kercem",
        "munxar",
        "xewkija",
        "xlendi",
        "sannat",
        "nadur",
        "xaghra",
        "qala",
        "ghasri",
        "fontana",
        "san lawrenz",
        "victoria",
        "marsalforn",
        "mgarr ix-xini",
        "ghajnsielem",
        "zebbug gozo",
        "rabat gozo",
        "mgarr gozo",
    }
)


def _normalize(text: str) -> str:
    """Lowercase, strip accents, collapse punctuation to spaces."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_ish = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    ascii_ish = ascii_ish.replace("ħ", "h").replace("Ħ", "h")
    ascii_ish = ascii_ish.lower()
    ascii_ish = re.sub(r"[^a-z0-9]+", " ", ascii_ish)
    return re.sub(r"\s+", " ", ascii_ish).strip()


def is_gozo_listing(
    *,
    title: str | None = None,
    locality: str | None = None,
    raw_text: str | None = None,  # kept for API compat; not scanned (site chrome often says "Gozo")
    url: str | None = None,
) -> bool:
    """Return True if the listing is on Gozo (exclude from mainland Malta monitoring).

    Only title, locality, and URL are inspected — full page HTML/text often contains
    unrelated \"Gozo\" links in navigation and would false-positive.
    """
    del raw_text  # unused on purpose
    headline = _normalize(" ".join([title or "", locality or "", url or ""]))
    if "gozo" in headline or "ghawdex" in headline:
        return True

    loc = _normalize(locality or "")
    if not loc:
        return False

    if loc in _GOZO_ONLY_LOCALITIES:
        return True

    for name in _GOZO_ONLY_LOCALITIES:
        if loc == name or loc.startswith(name + " "):
            return True

    return False


def is_gozo_record(item: dict[str, Any]) -> bool:
    return is_gozo_listing(
        title=item.get("title"),
        locality=item.get("locality"),
        url=item.get("url"),
    )
