# AGENTS.md — Malta Housing AI

Onboarding for AI coding agents. Read this before changing code in a new chat.

## What this project is

Personal pipeline: scrape Malta property portals → stage JSON → parse with local Ollama (Qwen 2.5 7B) → UPSERT SQLite → optional local HTML browser.

**Do not invent a flat `scraper.py` / `parser.py` / `database.py` layout.** That legacy shape is gone. Everything lives under `malta_housing/`.

## Architecture (must match)

```
scrapers/*.py  →  data/scraped_listings.json  →  parsing/llm.py
                 →  data/parsed_listings.json  →  db/store.py
                 →  data/malta_properties.db   →  web/ (optional)
```

| Concern | Location |
| --- | --- |
| CLI | `malta_housing/cli.py` (`python -m malta_housing …`) |
| Paths | `malta_housing/paths.py` → always under `data/` |
| HTTP + staging I/O | `malta_housing/common.py` (`HttpClient`, `merge_staging`, …) |
| Schemas | `malta_housing/models.py` (`ScrapedListing`, `ParsedListing`, `SourceType`) |
| Scrapers | `malta_housing/scrapers/<portal>.py` |
| LLM parse | `malta_housing/parsing/llm.py` |
| DB write | `malta_housing/db/store.py` |
| DB read / UI API | `malta_housing/db/queries.py` + `malta_housing/web/` |

## Sources (complete list)

`SourceType` = `maltapark` | `ownersbest` | `djar` | `propertymarket`

When adding a portal:

1. Add scraper in `malta_housing/scrapers/<name>.py` mirroring `ownersbest.py` / `djar.py`
2. Extend `SourceType` in `models.py`
3. Wire `ensure_source`, `cli.py` choices + `cmd_scrape`, `scrapers/__init__.py`
4. Return `ScrapedListing` with `source=…`, merge via `merge_staging`
5. Filter Gozo with `is_gozo_listing` / `is_gozo_record`
6. Update `run_all.ps1` sources list + README / this file

## Staging contract

Every scraped item must have:

```json
{ "url", "title", "raw_text", "source", "scraped_at" }
```

Merge is **by URL** (newer wins). Never overwrite staging with a single-portal dump.

## Commands

```bash
python -m malta_housing init-db
python -m malta_housing scrape --source <portal> --pages 3
python -m malta_housing parse          # --force re-parses known URLs
python -m malta_housing db
python -m malta_housing run --source <portal> --pages 3   # scrape→parse→db (one portal)
python -m malta_housing serve          # http://127.0.0.1:8765
python -m malta_housing purge-gozo
```

Windows all-portals: `.\run_all.ps1 -Pages 3` (`-Force` → parse `--force`).

Use project venv: `venv\Scripts\python.exe` (Windows).

## HTTP / anti-bot notes

* Prefer `HttpClient` from `common.py` — do not invent ad-hoc `requests` sessions in scrapers.
* `impersonate="chrome124"` uses `curl_cffi` (needed for Property Market TLS blocks).
* SiteGround `HTTP 202` + `sg-captcha` is auto-solved (SHA1 PoW → `_I_` cookie) inside `HttpClient`.
* Property Market: listing URLs need trailing `/`; pagination needs full query + `pp=N` (not bare `?pp=N`).

## Conventions for agents

* Match existing scraper style (prints, random sleeps, Gozo skip, `if __name__ == "__main__"`).
* Keep runtime artefacts in `data/` only; do not commit DB/JSON dumps.
* Do not edit README/AGENTS unless the task changes architecture or commands.
* Prefer minimal diffs; wire new sources through CLI + `SourceType`.
* `database is locked`: stop `serve` / other DB users before `db` writes (SQLite).
* `run_all.ps1`: keep ASCII-safe (PowerShell 5.1 + UTF-8 without BOM breaks on emoji/Polish).

## Quick file map

* `requirements.txt` — pinned deps including `curl_cffi`
* `setup.ps1` — venv + install + `init-db`
* `run_all.ps1` — all scrapers → parse → db
* `to_gzira.csv` — locality → distance used by `distances.py`
* `scraper_propertymarket.py` — thin launcher only; real logic in package
