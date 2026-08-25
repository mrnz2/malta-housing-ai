"""CLI orchestration: scrape → parse → db in one command."""

from __future__ import annotations

import argparse
import sys

from malta_housing.common import configure_stdio, ensure_source
from malta_housing.db.dhalia_urls import backfill_dhalia_urls
from malta_housing.scrapers.dhalia import refresh_dhalia_staging_areas
from malta_housing.db.store import (
    backfill_area_sqm,
    clear_evaluations,
    delete_gozo_listings,
    delete_out_of_budget_listings,
    init_db,
    load_parsed_and_save,
)
from malta_housing.parsing.llm import run_parser
from malta_housing.paths import DB_PATH
from malta_housing.scrapers.alliance import run_alliance_scraper
from malta_housing.scrapers.belair import run_belair_scraper
from malta_housing.scrapers.dhalia import run_dhalia_scraper
from malta_housing.scrapers.djar import run_djar_scraper
from malta_housing.scrapers.excelhomes import run_excelhomes_scraper
from malta_housing.scrapers.franksalt import run_franksalt_scraper
from malta_housing.scrapers.maltapark import run_scraper
from malta_housing.scrapers.ownersbest import run_ownersbest_scraper
from malta_housing.scrapers.propertymarket import run_propertymarket_scraper
from malta_housing.scrapers.re316 import run_re316_scraper
from malta_housing.scrapers.remax import run_remax_scraper
from malta_housing.scrapers.sensar import run_sensar_scraper
from malta_housing.scrapers.simonmamo import run_simonmamo_scraper
from malta_housing.scrapers.yitaku import run_yitaku_scraper
from malta_housing.analysis.ranker import run_rank
from malta_housing.i18n.translate import run_translate
from malta_housing.parsing.text_normalize import run_normalize_localities, run_normalize_titles
from malta_housing.web.server import run_server


def cmd_init_db(_args: argparse.Namespace) -> None:
    init_db()
    print(f"✅ Schema ready in {DB_PATH}")


def cmd_backfill_dhalia_urls(args: argparse.Namespace) -> None:
    stats = backfill_dhalia_urls(dry_run=args.dry_run)
    mode = "DRY-RUN" if args.dry_run else "DONE"
    print(f"🔗 [{mode}] Dhalia URL backfill")
    print(f"   total={stats['total']} updated={stats['updated']} already_ok={stats['already_ok']}")
    print(
        f"   duplicates_removed={stats['duplicates_removed']} "
        f"no_ref={stats['no_ref']} api_failed={stats['api_failed']}"
    )
    print(f"   json: staging={stats['json_staging']} parsed={stats['json_parsed']}")


def cmd_backfill_area(args: argparse.Namespace) -> None:
    if getattr(args, "refresh_dhalia", False):
        stats = refresh_dhalia_staging_areas()
        print(
            f"📐 Dhalia staging refresh: total={stats['total']} "
            f"updated={stats['updated']} api_failed={stats['api_failed']}"
        )
    stats = backfill_area_sqm(db_name=DB_PATH)
    print(
        f"📐 Backfill area_sqm: updated={stats['updated']} "
        f"(from text={stats['from_text']}, from evaluation={stats['from_evaluation']}), "
        f"already set={stats['already_set']}, still missing={stats['still_missing']}, "
        f"no staging text={stats['no_staging_text']}, total listings={stats['total']}"
    )


def cmd_scrape(args: argparse.Namespace) -> None:
    source = ensure_source(args.source)
    if source == "maltapark":
        run_scraper(max_pages=args.pages)
    elif source == "ownersbest":
        run_ownersbest_scraper(max_pages=args.pages)
    elif source == "propertymarket":
        run_propertymarket_scraper(max_pages=args.pages)
    elif source == "yitaku":
        run_yitaku_scraper(max_pages=args.pages)
    elif source == "remax":
        run_remax_scraper(max_pages=args.pages)
    elif source == "simonmamo":
        run_simonmamo_scraper(max_pages=args.pages)
    elif source == "belair":
        run_belair_scraper(max_pages=args.pages)
    elif source == "re316":
        run_re316_scraper(max_pages=5 if args.pages == 3 else args.pages)
    elif source == "franksalt":
        run_franksalt_scraper(max_pages=5 if args.pages == 3 else args.pages)
    elif source == "sensar":
        run_sensar_scraper(max_pages=5 if args.pages == 3 else args.pages)
    elif source == "excelhomes":
        run_excelhomes_scraper(max_pages=args.pages)
    elif source == "dhalia":
        run_dhalia_scraper(max_pages=args.pages)
    elif source == "alliance":
        run_alliance_scraper(max_pages=args.pages)
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
    if not args.skip_rank:
        run_rank(
            top=args.top,
            max_price=args.max_price,
            source=args.source,
            force=args.force_eval,
        )


def cmd_rank(args: argparse.Namespace) -> None:
    run_rank(
        top=args.top,
        max_price=args.max_price,
        source=args.source,
        force=args.force,
        new_only=args.new_only,
    )


def cmd_normalize_titles(_args: argparse.Namespace) -> None:
    run_normalize_titles()


def cmd_normalize_localities(_args: argparse.Namespace) -> None:
    run_normalize_localities()


def cmd_translate(args: argparse.Namespace) -> None:
    listings = not args.evaluations_only
    evaluations = not args.listings_only
    try:
        run_translate(
            force=args.force,
            listings=listings,
            evaluations=evaluations,
            url=args.url,
        )
    except KeyboardInterrupt:
        print("\n⏹ Translation interrupted.")


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


def cmd_serve(args: argparse.Namespace) -> None:
    run_server(host=args.host, port=args.port)


def cmd_purge_scores(_args: argparse.Namespace) -> None:
    stats = clear_evaluations()
    print(
        f"✅ Score purge complete "
        f"(evaluations_deleted={stats['evaluations_deleted']}, "
        f"listings_cleared={stats['listings_cleared']})."
    )


def cmd_purge_budget(_args: argparse.Namespace) -> None:
    from malta_housing.budget import is_out_of_budget
    from malta_housing.common import PARSED_PATH, load_json_list, save_json_list

    stats = delete_out_of_budget_listings()
    items = load_json_list(PARSED_PATH)
    if items:
        kept = [item for item in items if not is_out_of_budget(item)]
        removed = len(items) - len(kept)
        if removed:
            save_json_list(PARSED_PATH, kept)
            print(f"🧹 Usunięto {removed} ofert poza budżetem z {PARSED_PATH.name}.")
    print(
        f"✅ Budget purge complete "
        f"(deleted={stats['deleted']}, below_min={stats['below_min']}, "
        f"above_max={stats['above_max']})."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="malta_housing",
        description="Malta Housing AI — scrape, parse, and load listings",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="Create/migrate SQLite schema only")
    p_init.set_defaults(func=cmd_init_db)

    p_backfill_area = sub.add_parser(
        "backfill-area",
        help="Fill listings.area_sqm from scraped listing text (scraped_listings.json)",
    )
    p_backfill_area.add_argument(
        "--refresh-dhalia",
        action="store_true",
        help="Re-fetch Dhalia detail API into staging before backfilling area_sqm",
    )
    p_backfill_area.set_defaults(func=cmd_backfill_area)

    p_backfill_dhalia = sub.add_parser(
        "backfill-dhalia-urls",
        help="Rewrite legacy Dhalia property?ref= URLs to canonical /buy/.../Ref paths",
    )
    p_backfill_dhalia.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned changes without writing DB/JSON",
    )
    p_backfill_dhalia.set_defaults(func=cmd_backfill_dhalia_urls)

    p_scrape = sub.add_parser("scrape", help="Scrape a portal into scraped_listings.json (merge)")
    p_scrape.add_argument(
        "--source",
        required=True,
        choices=[
            "maltapark",
            "ownersbest",
            "djar",
            "propertymarket",
            "yitaku",
            "remax",
            "simonmamo",
            "belair",
            "re316",
            "franksalt",
            "sensar",
            "excelhomes",
            "dhalia",
            "alliance",
        ],
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

    p_run = sub.add_parser("run", help="Full pipeline: scrape → parse → db → rank")
    p_run.add_argument(
        "--source",
        required=True,
        choices=[
            "maltapark",
            "ownersbest",
            "djar",
            "propertymarket",
            "yitaku",
            "remax",
            "simonmamo",
            "belair",
            "re316",
            "franksalt",
            "sensar",
            "excelhomes",
            "dhalia",
            "alliance",
        ],
        help="Portal to scrape",
    )
    p_run.add_argument("--pages", type=int, default=3, help="Number of listing pages")
    p_run.add_argument(
        "--force",
        action="store_true",
        help="Force re-parse of known URLs",
    )
    p_run.add_argument(
        "--skip-rank",
        action="store_true",
        help="Skip AI evaluation after db (default: evaluate unevaluated listings)",
    )
    p_run.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of top listings to show after evaluation (default 10)",
    )
    p_run.add_argument(
        "--max-price",
        type=int,
        default=None,
        help="Only evaluate/rank listings at or below this EUR price",
    )
    p_run.add_argument(
        "--force-eval",
        action="store_true",
        help="Re-evaluate listings that already have cached scores",
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

    p_purge_budget = sub.add_parser(
        "purge-budget",
        help="Delete listings priced under €100k or over €400k from SQLite and parsed JSON",
    )
    p_purge_budget.set_defaults(func=cmd_purge_budget)

    p_purge_scores = sub.add_parser(
        "purge-scores",
        help="Clear all AI scores and evaluations from SQLite (listings remain)",
    )
    p_purge_scores.set_defaults(func=cmd_purge_scores)

    p_rank = sub.add_parser(
        "rank",
        help="AI investment ranking via Ollama (qwen2.5:7b); caches scores in SQLite",
    )
    p_rank.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of top listings to display (default 10)",
    )
    p_rank.add_argument(
        "--max-price",
        type=int,
        default=None,
        help="Only consider listings at or below this EUR price (e.g. 300000)",
    )
    p_rank.add_argument(
        "--source",
        default=None,
        choices=[
            "maltapark",
            "ownersbest",
            "djar",
            "propertymarket",
            "yitaku",
            "remax",
            "simonmamo",
            "belair",
            "re316",
            "franksalt",
            "sensar",
            "excelhomes",
            "dhalia",
            "alliance",
        ],
        help="Only evaluate listings from this portal (default: all sources)",
    )
    p_rank.add_argument(
        "--force",
        action="store_true",
        help="Re-evaluate listings that already have cached scores",
    )
    p_rank.add_argument(
        "--new-only",
        action="store_true",
        help="Only evaluate listings scraped on the latest scrape day",
    )
    p_rank.set_defaults(func=cmd_rank)

    p_translate = sub.add_parser(
        "translate",
        help="Translate existing English DB text fields to Polish via Ollama (no re-scrape)",
    )
    p_translate.add_argument(
        "--force",
        action="store_true",
        help="Re-translate even when Polish columns are already filled",
    )
    p_translate.add_argument(
        "--listings-only",
        action="store_true",
        help="Only translate listing fields (title, key_features)",
    )
    p_translate.add_argument(
        "--evaluations-only",
        action="store_true",
        help="Only translate AI evaluation fields (summary, pros, cons, warnings)",
    )
    p_translate.add_argument(
        "--url",
        default=None,
        help="Translate a single listing by URL",
    )
    p_translate.set_defaults(func=cmd_translate)

    p_normalize_titles = sub.add_parser(
        "normalize-titles",
        help="Normalize title casing (no LLM) for visible listings in DB and parsed JSON",
    )
    p_normalize_titles.set_defaults(func=cmd_normalize_titles)

    p_normalize_localities = sub.add_parser(
        "normalize-localities",
        help="Normalize locality spellings (no LLM) in DB and parsed JSON",
    )
    p_normalize_localities.set_defaults(func=cmd_normalize_localities)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0
