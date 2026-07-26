"""Project and data path helpers."""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
DATA_DIR = PROJECT_ROOT / "data"

STAGING_PATH = DATA_DIR / "scraped_listings.json"
PARSED_PATH = DATA_DIR / "parsed_listings.json"
PARSE_FAILURES_PATH = DATA_DIR / "parse_failures.jsonl"
DB_PATH = DATA_DIR / "malta_properties.db"


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR
