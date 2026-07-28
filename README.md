# Malta Housing AI

An automated pipeline for scraping, processing, analyzing, and storing real estate listings in Malta. It uses a modular Python package combined with a local Large Language Model (**Qwen 2.5 7B** via **Ollama**) for unstructured data extraction, storing structured results in a SQLite database.

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
└─────────────────────────┘
             ▼
   data/malta_properties.db
```

Orchestration: `python -m malta_housing` (`run` / `scrape` / `parse` / `db` / `init-db` / `serve` / `purge-gozo`).

Windows one-shot for **all** portals: `.\run_all.ps1` (scrape each source → parse → db).

### 2. Package layout

```
malta-housing-ai/
├── malta_housing/           # Python package
│   ├── __main__.py          # python -m malta_housing
│   ├── cli.py               # argparse orchestration
│   ├── models.py            # ScrapedListing, ParsedListing, …
│   ├── paths.py             # data/ paths relative to project root
│   ├── common.py            # HTTP client, staging I/O, HTML helpers
│   ├── scrapers/
│   │   ├── maltapark.py
│   │   ├── ownersbest.py
│   │   ├── djar.py
│   │   ├── propertymarket.py
│   │   └── yitaku.py
│   ├── parsing/
│   │   └── llm.py
│   ├── db/
│   │   ├── store.py         # UPSERT + price_history
│   │   └── queries.py       # read API for the browser
│   └── web/
│       ├── server.py        # local HTTP server (stdlib)
│       └── static/          # HTML / CSS / JS UI
├── data/                    # runtime artefacts (gitignored contents)
├── AGENTS.md                # onboarding for AI coding agents
├── run_all.ps1              # Windows: all scrapers → parse → db
├── run_pipeline.py          # thin wrapper → malta_housing.cli
├── scraper_propertymarket.py  # thin launcher for Property Market
├── requirements.txt
├── setup.ps1
└── README.md
```

* `malta_housing/models.py`: Shared Pydantic contracts (`ScrapedListing`, `MaltaPropertySchema`, `ParsedListing`).
* `malta_housing/common.py`: Shared HTTP client (session + retry on 429/5xx; optional `curl_cffi` TLS impersonation; SiteGround PoW auto-solve), staging merge I/O, HTML text helpers.
* `malta_housing/scrapers/maltapark.py`: Scrapes **MaltaPark** (`source=maltapark`).
* `malta_housing/scrapers/ownersbest.py`: Scrapes **Owners Best** (`source=ownersbest`).
* `malta_housing/scrapers/djar.py`: Scrapes **Djar.ai** (`source=djar`).
* `malta_housing/scrapers/propertymarket.py`: Scrapes **Property Market Malta** (`source=propertymarket`).
* `malta_housing/scrapers/yitaku.py`: Scrapes **Yitaku** JSON API (`source=yitaku`, €100k–€400k).
* `malta_housing/parsing/llm.py`: Ollama extraction with checkpoints; skips URLs already in DB (unless `--force`).
* `malta_housing/db/store.py`: UPSERTs into `data/malta_properties.db`; logs price changes in `price_history`.
* `malta_housing/web/`: Local browser UI — filter/search listings, open detail + price history.
* `setup.ps1`: Windows PowerShell install (venv + pinned deps + `init-db` only).
* `run_all.ps1`: Windows PowerShell — all scrapers in sequence, then parse, then db.

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
| `source` | `maltapark \| ownersbest \| djar \| propertymarket \| yitaku \| null` | Portal origin |
| `scraped_at` | `str \| null` | ISO timestamp from scrape |
| `updated_at` | `str \| null` | ISO timestamp of last parse/DB write |
| `distance_to_gzira_km` | `float \| null` | Estimated km to Gżira from `to_gzira.csv` |

**SQLite:** `data/malta_properties.db` — table `listings` (unique `url`) + `price_history` (`url`, `price_eur`, `recorded_at`) written on insert and whenever `price_eur` changes.

---

## Quick Start & Installation

### Prerequisites

* Python 3.10+
* Git
* Ollama installed locally with the Qwen 2.5 7B model:

```bash
ollama pull qwen2.5:7b
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

Ensure Ollama is running before `parse`.

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

### Browse the database (local UI)

```bash
python -m malta_housing serve
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). Optional: `--host 0.0.0.0 --port 8765`.

No extra dependencies — stdlib HTTP server + static HTML/JS reading `data/malta_properties.db`.

If `python -m malta_housing db` fails with `database is locked`, stop `serve` (or any other process using the DB) and retry.

---

## License

Internal / Personal Project — Designed for Malta Real Estate Market Analysis.
