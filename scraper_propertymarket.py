"""Thin launcher for Property Market scraper (package entry point)."""

from malta_housing.scrapers.propertymarket import run_propertymarket_scraper

if __name__ == "__main__":
    run_propertymarket_scraper(max_pages=3)
