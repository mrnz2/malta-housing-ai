"""CLI orchestration: scrape → parse → db in one command."""

from __future__ import annotations

import argparse
import sys

from malta_housing.common import configure_stdio, ensure_source
from malta_housing.db.store import delete_gozo_listings, init_db, load_parsed_and_save
from malta_housing.parsing.llm import run_parser
from malta_housing.paths import DB_PATH
from malta_housing.scrapers.djar import run_djar_scraper
from malta_housing.scrapers.maltapark import run_scraper
from malta_housing.scrapers.ownersbest import run_ownersbest_scraper
from malta_housing.scrapers.propertymarket import run_propertymarket_scraper
from malta_housing.web.server import run_server


def cmd_init_db(_args: argparse.Namespace) -> None:
    init_db()
    print(f"✅ Schema ready in {DB_PATH}")


def cmd_scrape(args: argparse.Namespace) -> None:
    source = ensure_source(args.source)
    if source == "maltapark":
        run_scraper(max_pages=args.pages)
    elif source == "ownersbest":
        run_ownersbest_scraper(max_pages=args.pages)
    elif source == "propertymarket":
        run_propertymarket_scraper(max_pages=args.pages)
    else:
        run_djar_scraper(max_pages=args.pages)


def cmd_parse(args: argparse.Namespace) -> None:
    run_parser(force=args.force)


def cmd_db(_args: argparse.Namespace) -> None:
    load_parsed_and_save()


def cmd_run(args: argparse.Namespace) -> None:
    cmd_scrape(args)
    run_parser(force=args.force)
    load_parsed_and_save()


def cmd_serve(args: argparse.Namespace) -> None:
    run_server(host=args.host, port=args.port)


def cmd_purge_gozo(_args: argparse.Namespace) -> None:
    from malta_housing.common import (
        PARSED_PATH,
        STAGING_PATH,
        load_json_list,
        save_json_list,
    )
    from malta_housing.geo import is_gozo_record

    stats = delete_gozo_listings()
    for path in (STAGING_PATH, PARSED_PATH):
        items = load_json_list(path)
        if not items:
            continue
        kept = [item for item in items if not is_gozo_record(item)]
        removed = len(items) - len(kept)
        if removed:
            save_json_list(path, kept)
            print(f"🧹 Usunięto {removed} ofert Gozo z {path.name}.")
    print(f"✅ Purge complete (DB deleted={stats['deleted']}).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="malta_housing",
        description="Malta Housing AI — scrape, parse, and load listings",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="Create/migrate SQLite schema only")
    p_init.set_defaults(func=cmd_init_db)

    p_scrape = sub.add_parser("scrape", help="Scrape a portal into scraped_listings.json (merge)")
    p_scrape.add_argument(
        "--source",
        required=True,
        choices=["maltapark", "ownersbest", "djar", "propertymarket"],
        help="Portal to scrape",
    )
    p_scrape.add_argument("--pages", type=int, default=3, help="Number of listing pages")
    p_scrape.set_defaults(func=cmd_scrape)

    p_parse = sub.add_parser("parse", help="Parse staging with Ollama into parsed_listings.json")
    p_parse.add_argument(
        "--force",
        action="store_true",
        help="Re-parse URLs already in DB / checkpoint",
    )
    p_parse.set_defaults(func=cmd_parse)

    p_db = sub.add_parser("db", help="UPSERT parsed_listings.json into SQLite")
    p_db.set_defaults(func=cmd_db)

    p_run = sub.add_parser("run", help="Full pipeline: scrape → parse → db")
    p_run.add_argument(
        "--source",
        required=True,
        choices=["maltapark", "ownersbest", "djar", "propertymarket"],
        help="Portal to scrape",
    )
    p_run.add_argument("--pages", type=int, default=3, help="Number of listing pages")
    p_run.add_argument(
        "--force",
        action="store_true",
        help="Force re-parse of known URLs",
    )
    p_run.set_defaults(func=cmd_run)

    p_serve = sub.add_parser(
        "serve",
        help="Local HTML browser for the SQLite listings database",
    )
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8765, help="Bind port (default 8765)")
    p_serve.set_defaults(func=cmd_serve)

    p_purge = sub.add_parser(
        "purge-gozo",
        help="Delete Gozo listings from the SQLite database",
    )
    p_purge.set_defaults(func=cmd_purge_gozo)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0
