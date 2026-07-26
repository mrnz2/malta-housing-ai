# Malta Housing AI 🇲🇹🏠

An automated pipeline for scraping, processing, analyzing, and storing real estate listings in Malta. It uses a modular Python architecture combined with a local Large Language Model (**Qwen 2.5 7B** via **Ollama**) for unstructured data extraction, storing structured results in a SQLite database.

---

## 🤖 System Context & Architecture Reference (For AI Assistants)

> **Note for LLMs:** Read this section to understand the project architecture, file conventions, data schema, and system workflows before assisting with code modifications or features.

### 1. Core Workflow Architecture
[ Web Portals ]
│
▼
┌─────────────────────────┐
│  Scrapers               │ -> scraper.py (MaltaPark)
│  (Requests + BS4)       │ -> scraper_ownersbest.py (Owners Best)
└────────────┬────────────┘
│ Saves raw payloads
▼
scraped_listings.json  (Intermediate Staging Schema)
│
▼
┌─────────────────────────┐
│  Parser                 │ -> Uses local Ollama (qwen2.5:7b) + Pydantic
│  (parser.py)            │    Extracts: price, town, bedrooms, airspace, freehold, etc.
└────────────┬────────────┘
│ Saves structured records
▼
parsed_listings.json
│
▼
┌─────────────────────────┐
│  Database               │ -> database.py
│  (SQLite)               │    Upserts into listings table (unique URL constraint)
└─────────────────────────┘

### 2. File & Directory Responsibilities
* `scraper.py`: Scrapes real estate listings from **MaltaPark**. Output format: List of `{ "url", "title", "raw_text" }`.
* `scraper_ownersbest.py`: Scrapes real estate listings from **Owners Best**. Handles pagination (`?pg=X`) and filter extraction (`/malta-property/` & `real-estate-detail-`).
* `debug_ownersbest.py` & `debug_links.py`: Diagnostic utilities for inspecting network responses and raw link extraction patterns.
* `parser.py`: Connects to `localhost:11434` (Ollama), sends `raw_text` to `qwen2.5:7b` using strict Pydantic schemas, and outputs clean structured JSON (`parsed_listings.json`).
* `database.py`: Reads `parsed_listings.json` and inserts/updates records in `listings.db` (SQLite).
* `setup.ps1`: Automated installation and setup script for Windows PowerShell environments.
* `.gitignore`: Excludes `venv/`, SQLite databases (`*.db`), and intermediate JSON staging files (`scraped_listings.json`, `parsed_listings.json`).

### 3. Data Schema Standards

**Staging Intermediate Schema (`scraped_listings.json`):**
```json
[
  {
    "url": "https://...",
    "title": "Property Title",
    "raw_text": "Full scraped body text..."
  }
]

Pydantic Data Model (Listing):

url (str, Primary Key / Unique)

title (str)

price (float | null)

location (str | null - Town/Village in Malta)

bedrooms (int | null)

bathrooms (int | null)

property_type (str | null - Apartment, Penthouse, Maisonette, Townhouse, etc.)

has_airspace (bool | null)

is_freehold (bool | null)

seller_type (str | null - Owner / Agent)

🚀 Quick Start & Installation
Prerequisites
Python 3.10+

Git

Ollama installed locally with the Qwen 2.5 7B model:

ollama pull qwen2.5:7b


🛠️ Setup Instructions
🪟 Windows (Automatic Setup)
Run the automated PowerShell setup script:

PowerShell
.\setup.ps1
Or manually:

PowerShell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install requests beautifulsoup4 pydantic
🍏 macOS / Linux
Open terminal in the project directory:

Bash
python3 -m venv venv
source venv/bin/activate
pip install requests beautifulsoup4 pydantic
💻 Running the Pipeline
Ensure Ollama is running in the background before starting the parser.

1. Activate Environment
Windows: .\venv\Scripts\Activate.ps1

macOS/Linux: source venv/bin/activate

2. Run Scraper
Choose a target platform:

MaltaPark:

Bash
python scraper.py
Owners Best:

Bash
python scraper_ownersbest.py
3. Parse Data with Local AI
Extract structured attributes using Ollama:

Bash
python parser.py
4. Load into SQLite Database
Save the extracted listings into local storage:

Bash
python database.py
🛠️ Diagnostics & Troubleshooting
If a scraper returns 0 listings due to layout changes or blocking, use the diagnostic scripts:

Bash
# Check HTTP status codes and initial page response
python debug_ownersbest.py

# Extract and inspect all raw links on the page
python debug_links.py
📝 License
Internal / Personal Project — Designed for Malta Real Estate Market Analysis.