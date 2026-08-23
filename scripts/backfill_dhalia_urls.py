"""One-time backfill of Dhalia listing URLs to canonical /buy/.../Ref paths."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from malta_housing.db.dhalia_urls import backfill_dhalia_urls


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    stats = backfill_dhalia_urls(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "DONE"
    print(f"\n🔗 [{mode}] Dhalia URL backfill")
    print(f"   total={stats['total']} updated={stats['updated']} already_ok={stats['already_ok']}")
    print(
        f"   duplicates_removed={stats['duplicates_removed']} "
        f"no_ref={stats['no_ref']} api_failed={stats['api_failed']}"
    )
    print(f"   json: staging={stats['json_staging']} parsed={stats['json_parsed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
