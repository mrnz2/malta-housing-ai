# AGENTS.md — Malta Housing AI

Onboarding for AI coding agents. Read this before changing code in a new chat.

## What this project is

Personal pipeline: scrape Malta property portals → stage JSON → parse with local Ollama (Qwen 2.5 7B) → UPSERT SQLite → optional **hybrid** AI investment ranking (Python base score + LLM adjustment) → optional local HTML browser.

**Do not invent a flat `scraper.py` / `parser.py` / `database.py` layout.** That legacy shape is gone. Everything lives under `malta_housing/`.

## Architecture (must match)

```
scrapers/*.py  →  data/scraped_listings.json  →  parsing/llm.py
                 →  data/parsed_listings.json  →  db/store.py
                 →  data/malta_properties.db   →  analysis/ (rank)
                                              →  web/ (optional)
```

| Concern | Location |
| --- | --- |
| CLI | `malta_housing/cli.py` (`python -m malta_housing …`) |
| Paths | `malta_housing/paths.py` → always under `data/` |
| HTTP + staging I/O | `malta_housing/common.py` (`HttpClient`, `merge_staging`, …) |
| Schemas | `malta_housing/models.py` (`ScrapedListing`, `ParsedListing`, `SourceType`) |
| Scrapers | `malta_housing/scrapers/<portal>.py` |
| LLM parse | `malta_housing/parsing/llm.py` |
| Base score rubric | `malta_housing/analysis/scoring.py` (`compute_base_score`) |
| AI evaluation | `malta_housing/analysis/evaluator.py` (`evaluate_listing`) |
| Rank orchestration | `malta_housing/analysis/ranker.py` (`run_rank`) |
| DB write | `malta_housing/db/store.py` |
| DB read / UI API | `malta_housing/db/queries.py` + `malta_housing/web/` |
| Budget band | `malta_housing/budget.py` (€100k–€400k) |
| Gżira / sea proximity | `malta_housing/distances.py` + `to_gzira.csv` |
| Gozo filter | `malta_housing/geo.py` |

## Sources (complete list)

`SourceType` = `maltapark` | `ownersbest` | `djar` | `propertymarket` | `yitaku` | `remax` | `simonmamo`

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

`raw_text` is also required for AI `rank` / `evaluate_listing` (loaded from staging by URL).

## SQLite schema

* `listings` — property fields + `distance_to_gzira_km`, `sea_proximity`, denormalized `ai_score`, `ai_summary`, `ai_evaluated_at`
* `price_history` — price change log
* `evaluations` — full AI result per URL (`pros`, `cons`, `evaluation_json`)

`save_evaluation()` UPSERTs `evaluations` and updates matching `listings` row.

## Commands

```bash
python -m malta_housing init-db
python -m malta_housing scrape --source <portal> --pages 3
python -m malta_housing parse          # --force re-parses known URLs
python -m malta_housing db
python -m malta_housing run --source <portal> --pages 3   # scrape→parse→db→rank (one portal)
python -m malta_housing rank --top 10 --max-price 300000  # AI investment ranking
python -m malta_housing serve          # http://127.0.0.1:8765
python -m malta_housing purge-gozo
python -m malta_housing purge-budget
```

Windows all-portals: `.\run_all.ps1 -Pages 3` (`-Force` → parse `--force`).

Use project venv: `venv\Scripts\python.exe` (Windows).

## AI evaluation (hybrid)

* Model: `qwen2.5:7b` via Ollama (same as parse)
* **Base score** (`scoring.py`, 0–8): deterministic from `price_per_sqm`, `distance_to_gzira_km`, `sea_proximity` (from `to_gzira.csv`: `nad_morzem` / `blisko` / `daleko`), `area_sqm` (regex from `raw_text`), and flags (`is_freehold`, `has_airspace`, `is_shell_form`, `seller_type`)
* **LLM adjustment** (`evaluator.py`, −2 … +2): qualitative risks/opportunities from `raw_text` only — emphyteusis, leasehold, dampness, no lift, hidden costs, etc.
* **Final score**: `clamp(base_score + qualitative_adjustment, 0, 10)` → stored as `ai_score` / `investment_score`
* Entry point: `evaluate_listing(listing: ParsedListing, raw_text: str) -> dict`
* Returns: `investment_score`, `base_score`, `qualitative_adjustment`, `score_breakdown`, `pros`, `cons`, `summary`, `metrics`
* `run_rank()` in `ranker.py`: fetch DB candidates → evaluate uncached → `save_evaluation()` → console report
* Web UI reads `ai_score` from `listings`; detail view joins `evaluations` for pros/cons and score breakdown from `evaluation_json`
* Default browser sort: `ai_score_desc`
* After rubric changes: `python -m malta_housing rank --force` to refresh cached scores
* `init-db` backfills `distance_to_gzira_km` and `sea_proximity` from locality + `to_gzira.csv`

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
* `database is locked`: stop `serve` / other DB users before `db` or `rank` writes (SQLite).
* `run_all.ps1`: keep ASCII-safe (PowerShell 5.1 + UTF-8 without BOM breaks on emoji/Polish).

## Quick file map

* `requirements.txt` — pinned deps including `curl_cffi`, `ollama`
* `setup.ps1` — venv + install + `init-db`
* `run_all.ps1` — all scrapers → parse → db
* `to_gzira.csv` — locality profiles: km to Gżira + sea proximity (`Dystans do morza`)
* `scraper_propertymarket.py` — thin launcher only; real logic in package
* `malta_housing/analysis/scoring.py` — deterministic base score rubric
* `malta_housing/analysis/evaluator.py` — hybrid scorer (base + LLM adjustment)
* `malta_housing/analysis/ranker.py` — batch rank CLI logic
