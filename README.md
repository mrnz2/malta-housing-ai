# Malta Housing AI

An automated pipeline for scraping, processing, analyzing, and storing real estate listings in Malta. It uses a modular Python package combined with local LLMs via **Ollama**: **Qwen 2.5 7B** for unstructured data extraction and investment scoring, plus **Bielik 4.5B** for EN→PL translation in the browser. A **hybrid investment scorer** (deterministic Python rubric + LLM qualitative adjustment) stores structured results in SQLite.

> **For AI assistants / new chats:** start with [`AGENTS.md`](AGENTS.md) (also loaded via `.cursor/rules/`).

---

## System Context & Architecture Reference

### 1. Core Workflow Architecture

```
[ Web Portals ]
        │
        ▼
┌─────────────────────────┐
│  scrapers/              │ -> maltapark.py
│  (HTTP + BS4)           │ -> ownersbest.py
│                         │ -> djar.py
│                         │ -> propertymarket.py
│                         │ -> yitaku.py
│                         │ -> remax.py
│                         │ -> simonmamo.py
│                         │ -> belair.py
│                         │ -> re316.py
│                         │ -> franksalt.py
│                         │ -> sensar.py
│                         │ -> excelhomes.py
└────────────┬────────────┘
             │ Merges raw payloads (by URL)
             ▼
   data/scraped_listings.json
             │
             ▼
┌─────────────────────────┐
│  parsing/               │ -> Ollama (qwen2.5:7b) + Pydantic models
│  (llm.py)               │    Retries, checkpoints, parse_failures.jsonl
└────────────┬────────────┘
             │ Saves structured records
             ▼
   data/parsed_listings.json
             │
             ▼
┌─────────────────────────┐
│  db/                    │ -> store.py
│  (SQLite)               │    UPSERT listings + price_history on price change
└────────────┬────────────┘
             ▼
   data/malta_properties.db
             │
             ▼
┌─────────────────────────┐
│  analysis/              │ -> scoring.py (deterministic base 0–8)
│  (optional)             │ -> evaluator.py (LLM adjustment + pros/cons)
│                         │ -> ranker.py (batch evaluate + console report)
└────────────┬────────────┘
             │ ai_score synced to listings + evaluations table
             ▼
┌─────────────────────────┐
│  web/                   │ -> local browser (scores, filters, sort, PL/EN)
└────────────┬────────────┘
             │ optional EN→PL backfill
             ▼
┌─────────────────────────┐
│  i18n/translate.py      │ -> Ollama (Bielik 4.5B) — title, features, AI text
└─────────────────────────┘
```

Orchestration: `python -m malta_housing` (`run` / `scrape` / `parse` / `db` / `rank` / `translate` / `init-db` / `serve` / `purge-gozo` / `purge-budget` / `purge-scores`).

Windows one-shot for **all** portals: `.\run_all.ps1` (scrape each source → parse → db → rank new listings).

### 2. Package layout

```
malta-housing-ai/
├── malta_housing/           # Python package
│   ├── __main__.py          # python -m malta_housing
│   ├── cli.py               # argparse orchestration
│   ├── models.py            # ScrapedListing, ParsedListing, …
│   ├── paths.py             # data/ paths relative to project root
│   ├── common.py            # HTTP client, staging I/O, HTML helpers
│   ├── budget.py            # €100k–€400k band helpers
│   ├── distances.py         # locality profiles from to_gzira.csv (Gżira km, sea proximity)
│   ├── geo.py               # Gozo detection / filtering
│   ├── scrapers/
│   │   ├── maltapark.py
│   │   ├── ownersbest.py
│   │   ├── djar.py
│   │   ├── propertymarket.py
│   │   ├── yitaku.py
│   │   ├── remax.py
│   │   ├── simonmamo.py
│   │   ├── belair.py
│   │   ├── re316.py
│   │   ├── franksalt.py
│   │   ├── sensar.py
│   │   └── excelhomes.py
│   ├── parsing/
│   │   └── llm.py
│   ├── analysis/
│   │   ├── scoring.py       # deterministic base score (price/m², Gżira, sea, area, flags)
│   │   ├── evaluator.py     # hybrid: base score + LLM qualitative adjustment
│   │   └── ranker.py        # batch rank CLI orchestration
│   ├── db/
│   │   ├── store.py         # UPSERT + price_history + evaluations
│   │   └── queries.py       # read API for the browser
│   ├── i18n/
│   │   └── translate.py     # EN→PL via Ollama (Bielik)
│   └── web/
│       ├── server.py        # local HTTP server (stdlib)
│       └── static/          # HTML / CSS / JS UI
├── data/                    # runtime artefacts (gitignored contents)
├── AGENTS.md                # onboarding for AI coding agents
├── run_all.ps1              # Windows: all scrapers → parse → db
├── run_pipeline.py          # thin wrapper → malta_housing.cli
├── scraper_propertymarket.py  # thin launcher for Property Market
├── to_gzira.csv             # locality → km to Gżira + sea proximity (nad morzem / blisko / daleko)
├── requirements.txt
├── setup.ps1
└── README.md
```

* `malta_housing/models.py`: Shared Pydantic contracts (`ScrapedListing`, `MaltaPropertySchema`, `ParsedListing`).
* `malta_housing/common.py`: Shared HTTP client (session + retry on 429/5xx; optional `curl_cffi` TLS impersonation; SiteGround PoW auto-solve), staging merge I/O, HTML text helpers.
* `malta_housing/scrapers/*.py`: Portal scrapers (`maltapark`, `ownersbest`, `djar`, `propertymarket`, `yitaku`, `remax`, `simonmamo`, `belair`, `re316`, `franksalt`, `sensar`, `excelhomes`, `dhalia`, `alliance`).
* `malta_housing/parsing/llm.py`: Ollama extraction with checkpoints; skips URLs already in DB (unless `--force`).
* `malta_housing/distances.py`: Locality profiles from `to_gzira.csv` — km to Gżira, sea proximity (`nad_morzem` / `blisko` / `daleko`), region.
* `malta_housing/analysis/scoring.py`: Deterministic **base score** (0–8) from price/m², distance to Gżira, sea proximity, area, and structural flags (freehold, airspace, shell, seller).
* `malta_housing/analysis/evaluator.py`: **Hybrid** investment scoring — Python base score + Ollama **qualitative adjustment** (−2 … +2) for text risks/opportunities; returns `investment_score` (0–10), `base_score`, `qualitative_adjustment`, `score_breakdown`, pros, cons, summary, metrics.
* `malta_housing/analysis/ranker.py`: Fetches DB candidates, evaluates unevaluated listings, prints ranked console report with score breakdown.
* `malta_housing/db/store.py`: UPSERTs into `data/malta_properties.db`; logs price changes in `price_history`; persists AI evaluations.
* `malta_housing/db/queries.py`: Read API for the browser (filtering, sorting by `ai_score`, stats).
* `malta_housing/i18n/translate.py`: EN→PL translation via Ollama Bielik; fills `*_pl` columns without re-scrape.
* `malta_housing/web/`: Local browser UI — filter/search listings, AI score column, sea proximity, sort by score, detail view with base/LLM breakdown, pros/cons, PL/EN toggle.
* `setup.ps1`: Windows PowerShell install (venv + pinned deps + `init-db` only).
* `run_all.ps1`: Windows PowerShell — all scrapers in sequence, then parse, db, and rank (`--new-only`).

### 3. Data Schema Standards

**Staging Intermediate Schema (`data/scraped_listings.json`):**

```json
[
  {
    "url": "https://...",
    "title": "Property Title",
    "raw_text": "Full scraped body text...",
    "source": "maltapark",
    "scraped_at": "2026-07-26T10:00:00+00:00"
  }
]
```

**Parsed / DB listing fields (`ParsedListing`):**

| Field | Type | Notes |
| --- | --- | --- |
| `url` | `str` | Unique key |
| `title` | `str` | |
| `price_eur` | `int \| null` | EUR as integer |
| `locality` | `str \| null` | Town/village in Malta |
| `bedrooms` | `int \| null` | |
| `property_type` | `str \| null` | Apartment, Penthouse, Maisonette, etc. |
| `has_airspace` | `bool` | |
| `is_freehold` | `bool` | |
| `has_sea_view` | `bool` | |
| `is_shell_form` | `bool` | |
| `seller_type` | `OWNER \| AGENT \| SENSAR \| UNKNOWN \| null` | |
| `key_features` | `list[str]` | Max ~4 features |
| `source` | `maltapark \| ownersbest \| djar \| propertymarket \| yitaku \| remax \| simonmamo \| belair \| re316 \| franksalt \| sensar \| excelhomes \| dhalia \| alliance \| null` | Portal origin |
| `scraped_at` | `str \| null` | ISO timestamp from scrape |
| `updated_at` | `str \| null` | ISO timestamp of last parse/DB write |
| `distance_to_gzira_km` | `float \| null` | Estimated km to Gżira from `to_gzira.csv` |
| `sea_proximity` | `nad_morzem \| blisko \| daleko \| null` | Sea distance category from `to_gzira.csv` |
| `ai_score` | `float \| null` | Final investment score 0–10 (denormalized from evaluations) |
| `ai_summary` | `str \| null` | Two-sentence executive summary |
| `ai_evaluated_at` | `str \| null` | ISO timestamp of last AI evaluation |

**SQLite (`data/malta_properties.db`):**

| Table | Purpose |
| --- | --- |
| `listings` | Unique `url`; property fields + `distance_to_gzira_km`, `sea_proximity`, `ai_score`, `ai_summary`, `ai_evaluated_at` |
| `price_history` | `url`, `price_eur`, `recorded_at` — written on insert and whenever `price_eur` changes |
| `evaluations` | Full AI result per URL: `ai_score`, `ai_summary`, `pros`, `cons`, `evaluation_json`, `evaluated_at` |

On each `rank` evaluation, both `evaluations` and `listings` are updated. Previously evaluated URLs are skipped unless `--force`. `purge-scores` wipes the cache so the next `rank` treats all listings as unevaluated.

---

## Quick Start & Installation

### Prerequisites

* Python 3.10+
* Git
* Ollama installed locally with two models:

```bash
ollama pull qwen2.5:7b                                              # parse + rank
ollama pull SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0               # EN→PL translate
```

### Setup

**Windows (automatic):**

```powershell
.\setup.ps1
```

**Manual (Windows / macOS / Linux):**

```bash
python -m venv venv
# Windows: .\venv\Scripts\Activate.ps1
# macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
python -m malta_housing init-db
```

### Running the pipeline

Ensure Ollama is running before `parse`, `rank`, or `translate`.

**All portals (Windows, recommended):**

```powershell
.\run_all.ps1 -Pages 3
# optional: .\run_all.ps1 -Pages 3 -Force   # re-parse known URLs
```

**Single portal (full scrape → parse → db):**

```bash
python -m malta_housing run --source maltapark --pages 3
python -m malta_housing run --source ownersbest --pages 3
python -m malta_housing run --source djar --pages 3
python -m malta_housing run --source propertymarket --pages 3
python -m malta_housing run --source yitaku --pages 3
python -m malta_housing run --source remax --pages 3
python -m malta_housing run --source simonmamo --pages 3
python -m malta_housing run --source belair --pages 3
python -m malta_housing run --source re316 --pages 5
python -m malta_housing run --source franksalt --pages 5
python -m malta_housing run --source sensar --pages 5
python -m malta_housing run --source excelhomes --pages 3
```

**Step by step:**

```bash
python -m malta_housing scrape --source maltapark --pages 3
python -m malta_housing parse          # add --force to re-parse known URLs
python -m malta_housing db
```

`python run_pipeline.py …` still works as a thin wrapper around the same CLI.

Staging merges by URL across portals — running MaltaPark then Owners Best accumulates both in `data/scraped_listings.json`.

Gozo listings are excluded (scrape / parse / db). To remove any already stored:

```bash
python -m malta_housing purge-gozo
```

Listings outside the €100k–€400k band can be purged:

```bash
python -m malta_housing purge-budget
```

To clear all AI scores and evaluation cache (listings remain; `ai_score` becomes `NULL`):

```bash
python -m malta_housing purge-scores
```

Use before a full re-rank with `rank --force`, or after rubric changes when you want every listing re-evaluated from scratch.

### AI investment ranking (hybrid)

Evaluate listings already in the database (requires `raw_text` in `scraped_listings.json` for each URL):

```bash
python -m malta_housing init-db    # backfill sea_proximity from to_gzira.csv
python -m malta_housing rank --top 10 --max-price 300000
python -m malta_housing rank --top 10 --max-price 300000 --force   # re-evaluate cached
```

The command:

1. Loads candidate listings from SQLite (optionally capped by `--max-price`)
2. Skips URLs already evaluated (unless `--force`)
3. Computes a **deterministic base score** (0–8) in Python from price/m², Gżira distance, sea proximity, area, and flags
4. Sends listing metadata + `raw_text` to Ollama for a **qualitative adjustment** (−2 … +2) and pros/cons/summary
5. Stores results in `evaluations` and syncs `ai_score` / `ai_summary` onto `listings`
6. Prints a ranked console report with base + LLM breakdown

**Scoring model:**

| Layer | Range | What it measures |
| --- | --- | --- |
| Base score (Python) | 0–8 | `price_per_sqm`, `distance_to_gzira_km`, `sea_proximity`, `area_sqm`, freehold/airspace/shell/OWNER |
| LLM adjustment (Ollama) | −2 … +2 | Text risks: emphyteusis, leasehold, dampness, no lift, hidden costs, renovation upside |
| **Final** | 0–10 | `clamp(base_score + qualitative_adjustment, 0, 10)` |

Tune thresholds in `malta_housing/analysis/scoring.py`. After changing the rubric, run `purge-scores` then `rank` (or `rank --force`) to refresh cached scores.

### Polish translation (Bielik)

After listings are in the database (and optionally ranked), translate English text fields to Polish without re-scraping:

```bash
python -m malta_housing translate
python -m malta_housing translate --force              # re-translate filled PL columns
python -m malta_housing translate --listings-only      # title + key_features only
python -m malta_housing translate --evaluations-only   # AI summary, pros, cons, warnings
python -m malta_housing translate --url "https://..."  # single listing
```

Uses **`SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0`** via Ollama (`malta_housing/i18n/translate.py`). Writes `title_pl`, `key_features_pl`, `ai_summary_pl`, and evaluation `*_pl` columns. Skips rows that already have Polish text unless `--force`. The web UI can also trigger per-listing translation (PL locale + **Translate** button in the detail panel).

**Evaluation JSON shape:**

```json
{
  "investment_score": 7.2,
  "base_score": 6.5,
  "qualitative_adjustment": 0.7,
  "score_breakdown": {
    "price_per_sqm": 2.0,
    "distance_to_gzira": 1.6,
    "sea_proximity": 1.5,
    "area_sqm": 1.0,
    "structured_flags": 0.4
  },
  "pros": ["Recent renovation", "Strong rental area"],
  "cons": ["Ground rent mentioned", "No lift"],
  "summary": "Two-sentence executive summary in English.",
  "metrics": {
    "price_eur": 285000,
    "distance_to_gzira_km": 1.2,
    "sea_proximity": "nad_morzem",
    "area_sqm": 95,
    "price_per_sqm": 3000
  }
}
```

### Browse the database (local UI)

```bash
python -m malta_housing serve
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). Optional: `--host 0.0.0.0 --port 8765`.

The browser shows:

* **Score** column per listing (`ai_score` / 10, or `—` if not evaluated)
* **Sea** column — proximity category from `to_gzira.csv` (nad morzem / blisko / daleko)
* Default sort: **AI score ↓** (click the Score header to toggle direction)
* **PL / EN** language toggle; detail panel shows localized title, features, and AI text when `*_pl` columns are filled
* Detail panel: final score with base + LLM breakdown, component scores, summary, pros, cons, price history; **Translate** button runs Bielik on demand
* Header stats: total listings, average price, scored count, average score

No extra dependencies — stdlib HTTP server + static HTML/JS reading `data/malta_properties.db`.

If `python -m malta_housing db` or `rank` fails with `database is locked`, stop `serve` (or any other process using the DB) and retry.

**Query scores directly:**

```bash
sqlite3 data/malta_properties.db "
SELECT title, price_eur, ai_score, ai_summary
FROM listings
WHERE ai_score IS NOT NULL
ORDER BY ai_score DESC;
"
```

---

## CLI reference

| Command | Description |
| --- | --- |
| `init-db` | Create / migrate SQLite schema |
| `scrape --source <portal> --pages N` | Scrape one portal into staging JSON |
| `parse [--force]` | Parse staging with Ollama |
| `db` | UPSERT parsed JSON into SQLite |
| `run --source <portal> --pages N [--force] [--skip-rank]` | scrape → parse → db → rank |
| `rank --top N [--max-price EUR] [--force]` | Hybrid AI investment ranking (Python base + Ollama adjustment) |
| `translate [--force] [--listings-only \| --evaluations-only] [--url URL]` | EN→PL via Bielik (no re-scrape) |
| `normalize-titles` | Fix title casing in DB + parsed JSON (no LLM) |
| `serve [--host HOST] [--port PORT]` | Local listings browser |
| `purge-gozo` | Remove Gozo listings from DB + JSON |
| `purge-budget` | Remove listings outside €100k–€400k |
| `purge-scores` | Clear all `evaluations` rows and NULL `ai_score` / `ai_summary` / `ai_evaluated_at` on listings |

---

## License

Internal / Personal Project — Designed for Malta Real Estate Market Analysis.
