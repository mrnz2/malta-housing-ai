"""SQLite persistence."""

from malta_housing.db.store import (
    delete_gozo_listings,
    delete_out_of_budget_listings,
    get_evaluated_urls,
    get_known_urls,
    init_db,
    load_parsed_and_save,
    save_evaluation,
    save_listings_to_db,
    set_listing_fav,
    set_listing_hidden,
    set_listing_notes,
)

__all__ = [
    "delete_gozo_listings",
    "delete_out_of_budget_listings",
    "get_evaluated_urls",
    "get_known_urls",
    "init_db",
    "load_parsed_and_save",
    "save_evaluation",
    "save_listings_to_db",
    "set_listing_fav",
    "set_listing_hidden",
    "set_listing_notes",
]
