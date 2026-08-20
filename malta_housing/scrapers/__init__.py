"""Portal scrapers."""

from malta_housing.scrapers.belair import run_belair_scraper
from malta_housing.scrapers.djar import run_djar_scraper
from malta_housing.scrapers.franksalt import run_franksalt_scraper
from malta_housing.scrapers.maltapark import run_scraper
from malta_housing.scrapers.ownersbest import run_ownersbest_scraper
from malta_housing.scrapers.propertymarket import run_propertymarket_scraper
from malta_housing.scrapers.re316 import run_re316_scraper
from malta_housing.scrapers.remax import run_remax_scraper
from malta_housing.scrapers.simonmamo import run_simonmamo_scraper
from malta_housing.scrapers.yitaku import run_yitaku_scraper

__all__ = [
    "run_belair_scraper",
    "run_scraper",
    "run_ownersbest_scraper",
    "run_djar_scraper",
    "run_propertymarket_scraper",
    "run_yitaku_scraper",
    "run_remax_scraper",
    "run_simonmamo_scraper",
    "run_re316_scraper",
    "run_franksalt_scraper",
]
