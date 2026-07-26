"""Portal scrapers."""

from malta_housing.scrapers.djar import run_djar_scraper
from malta_housing.scrapers.maltapark import run_scraper
from malta_housing.scrapers.ownersbest import run_ownersbest_scraper
from malta_housing.scrapers.propertymarket import run_propertymarket_scraper

__all__ = [
    "run_scraper",
    "run_ownersbest_scraper",
    "run_djar_scraper",
    "run_propertymarket_scraper",
]
