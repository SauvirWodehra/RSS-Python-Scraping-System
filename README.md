# 📡 RSS Python Scraping System

<div align="center">

![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%2B-336791?logo=postgresql&logoColor=white)
![Pandas](https://img.shields.io/badge/Pandas-3.x-150458?logo=pandas&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-Active-brightgreen)

**A fully automated, production-grade RSS feed scraping pipeline.**  
Collects articles from 13 news sources, extracts full content, cleans the data, and stores everything in PostgreSQL — running automatically every hour.

</div>

---

## What It Does

> Every hour, this pipeline automatically visits 13 news websites, reads their latest articles, extracts the full content, cleans the data, and saves everything neatly into a PostgreSQL database — ready for analysis.

No manual effort needed. The system handles collection, extraction, deduplication, cleaning, storage, and analytics completely automatically.

---

## Architecture

```
RSS Sources → RSS Collector → Article Extractor → Web Scraper → Data Cleaner → PostgreSQL DB → Analytics
```

```
┌─────────────┐    ┌──────────────┐    ┌───────────────────┐    ┌──────────────┐    ┌─────────────┐
│  13 RSS     │───▶│ RSS          │───▶│ Article Extractor │───▶│ Data Cleaner │───▶│ PostgreSQL  │
│  Feed URLs  │    │ Collector    │    │ Newspaper4k +     │    │ Pandas       │    │ rss_pipeline│
│             │    │ feedparser   │    │ BeautifulSoup     │    │              │    │             │
└─────────────┘    └──────────────┘    └───────────────────┘    └──────────────┘    └─────────────┘
                                                                                           │
                                                                                    ┌──────▼──────┐
                                                                                    │  Analytics  │
                                                                                    │  Reporter   │
                                                                                    │  + CSV      │
                                                                                    └─────────────┘
```

---

## Tech Stack

| Component | Library | Purpose |
|-----------|---------|---------|
| RSS Parsing | `feedparser` | Reads XML feeds from all 13 sources |
| Article Extraction | `newspaper4k` | Extracts full article text, author, date |
| HTML Fallback Scraping | `requests` + `beautifulsoup4` | Scrapes when Newspaper4k fails |
| Data Cleaning | `pandas` | Deduplication, normalisation, word count, language detection |
| Language Detection | `langdetect` | Identifies article language (ISO 639-1) |
| Database | `psycopg2` + PostgreSQL | Bulk upsert with duplicate prevention |
| Scheduling | `APScheduler` | Runs pipeline every 60 minutes automatically |
| Logging | Python `logging` | Rotating file + stdout logging |
| Config | `python-dotenv` | Environment-based credentials management |

---

## RSS Feed Sources (13 Feeds)

| Category | Sources |
|----------|---------|
| 🖥️ Technology | TechCrunch, The Verge, Wired, Ars Technica |
| 💰 Finance | Reuters Business, Yahoo Finance, MarketWatch |
| 📰 General News | BBC News, Reuters Top News, NPR News |
| 🔭 Science | NASA Breaking News, ScienceDaily, New Scientist |

---

## Project Structure

```
RSS_Scraping/
│
├── main.py                        # Entry point — run this
├── requirements.txt               # Python dependencies
├── .env.example                   # Environment variable template
├── COMMANDS.txt                   # Full command reference cheatsheet
│
├── config/
│   └── settings.py                # DB config, feed URLs, scheduler interval
│
├── db/
│   ├── schema.sql                 # PostgreSQL DDL (3 tables + indexes)
│   └── connection.py              # ThreadedConnectionPool + bulk upsert helpers
│
├── pipeline/
│   ├── rss_collector.py           # Stage 1: feedparser RSS reader
│   ├── article_extractor.py       # Stage 2: Newspaper4k full-text extractor
│   ├── web_scraper.py             # Stage 2b: BeautifulSoup HTML fallback
│   └── data_cleaner.py            # Stage 3: Pandas cleaning + CSV export
│
├── scheduler/
│   └── scheduler.py               # APScheduler — fires pipeline every 60 min
│
├── analytics/
│   └── reporter.py                # 6-section analytics report + CSV export
│
└── utils/
    └── logger.py                  # Rotating log (10 MB, 5 backups) + stdout
```

---

## Database Schema

### `rss_sources` — Feed registry
| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL PK | |
| `name` | TEXT | Feed name (e.g. "TechCrunch") |
| `url` | CITEXT UNIQUE | RSS feed URL |
| `category` | TEXT | Technology / Finance / Science / General |
| `is_active` | BOOLEAN | Enable / disable feed |
| `last_fetched_at` | TIMESTAMPTZ | Updated after each run |

### `articles` — All scraped articles
| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL PK | |
| `source_id` | INT FK | → rss_sources |
| `title` | TEXT | Article headline |
| `url` | CITEXT UNIQUE | Article URL (prevents duplicates) |
| `author` | TEXT | Author name(s) |
| `published_at` | TIMESTAMPTZ | Original publish time |
| `summary` | TEXT | RSS-provided summary |
| `full_text` | TEXT | Full scraped article body |
| `word_count` | INT | Words in full_text |
| `language` | TEXT | ISO 639-1 code (`en`, `fr`, etc.) |
| `is_clean` | BOOLEAN | Passed all cleaning checks |
| `scraped_at` | TIMESTAMPTZ | When row was inserted |

### `pipeline_runs` — Execution audit log
| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL PK | |
| `started_at` | TIMESTAMPTZ | Run start time |
| `finished_at` | TIMESTAMPTZ | Run end time |
| `articles_found` | INT | Total RSS entries fetched |
| `articles_inserted` | INT | New articles stored |
| `articles_skipped` | INT | Duplicates skipped |
| `errors` | INT | Error count |
| `status` | TEXT | `success` / `partial` / `failed` |

---

## Setup & Installation

### Prerequisites
- Python 3.12 or higher
- PostgreSQL 15 or higher

### 1. Clone the repository
```bash
git clone https://github.com/SauvirWodehra/RSS-Python-Scraping-System.git
cd RSS-Python-Scraping-System
```

### 2. Install dependencies
```bash
python -m pip install --prefer-binary -r requirements.txt
python -m pip install --prefer-binary newspaper4k lxml_html_clean
```

### 3. Configure environment
```bash
# Copy the example and fill in your PostgreSQL credentials
cp .env.example .env
```

Edit `.env`:
```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=rss_pipeline
DB_USER=postgres
DB_PASSWORD=your_postgres_password_here
SCHEDULER_INTERVAL_MINUTES=60
LOG_LEVEL=INFO
```

### 4. Create the PostgreSQL database
```sql
CREATE DATABASE rss_pipeline;
```

Or from terminal:
```bash
# Linux / Mac
psql -U postgres -c "CREATE DATABASE rss_pipeline;"

# Windows PowerShell
$env:PGPASSWORD='your_password'
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -c "CREATE DATABASE rss_pipeline;"
```

---

## Usage

### Run once (test / one-off scrape)
```bash
python main.py --once
```

### Run continuously (auto-repeats every 60 min)
```bash
python main.py
```
Press `Ctrl+C` to stop.

### View analytics report
```bash
python main.py --report
```

---

## Pipeline Stages

### Stage 1 — RSS Collector (`pipeline/rss_collector.py`)
- Iterates all 13 configured feed URLs
- Parses XML with `feedparser`
- Extracts: title, URL, summary, publish date, author per entry
- Adds 0.5s polite delay between feeds
- **Output:** ~300–400 raw article dicts per run

### Stage 2 — Article Extractor (`pipeline/article_extractor.py`)
- Visits each article URL to fetch full content
- **Primary:** Newspaper4k — extracts article body, author, date automatically
- **Fallback:** BeautifulSoup — scrapes `<p>` tags when Newspaper4k fails or is blocked
- **Output:** Enriched dicts with `full_text`, merged `author`, `published_at`

### Stage 3 — Data Cleaner (`pipeline/data_cleaner.py`)
Pandas-based cleaning pipeline:
1. Strip whitespace & decode HTML entities
2. Deduplicate by URL (keeps first occurrence)
3. Drop rows with no usable text content
4. Drop rows with title < 5 characters
5. Normalise dates to UTC
6. Compute `word_count`
7. Detect language (`langdetect`)
8. Export timestamped CSV to `exports/`
- **Output:** Clean list of dicts ready for DB insertion

### Stage 4 — Database Storage (`db/connection.py`)
- `ON CONFLICT (url) DO NOTHING` — fully idempotent, safe to re-run anytime
- ThreadedConnectionPool for thread-safe concurrent access
- Records run metadata in `pipeline_runs` table

---

## Analytics Report

Run `python main.py --report` to get:

| Section | What it shows |
|---------|--------------|
| Overall Stats | Total articles, active sources, avg word count, date range |
| Articles per Source | Count + avg words per feed |
| Articles per Day | Publication trend over last 30 days |
| Top Authors | Most prolific authors across all sources |
| Language Breakdown | % of articles per language |
| Pipeline Run History | Last 10 execution results |

---

## Adding a New RSS Feed

Edit `config/settings.py` — add an entry to `RSS_FEEDS`:

```python
{
    "name": "Hacker News",
    "url":  "https://hnrss.org/frontpage",
    "category": "Technology",
},
```

Re-run the pipeline — it auto-seeds the new source and starts collecting immediately.

---

## Key Design Decisions

| Decision | Reason |
|----------|--------|
| `ON CONFLICT DO NOTHING` on URL | Idempotent — safe to re-run any number of times |
| Newspaper4k → BS4 fallback | Never loses an article even when primary extractor is blocked |
| `ThreadedConnectionPool` | APScheduler runs jobs on threads — pool provides safe concurrent DB access |
| `pipeline_runs` audit table | Full history of every run with inserted/skipped/error counts |
| Rotating log (10 MB, 5 backups) | Logs never consume unbounded disk space |
| `.env` for credentials | Passwords never committed to version control |

---

## License

MIT License — free to use, modify, and distribute.

---

## Author

**Sauvir Wodehra**  
[GitHub](https://github.com/SauvirWodehra)
