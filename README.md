# 📡 AI-Powered RSS Python Scraping System

<div align="center">

![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%2B-336791?logo=postgresql&logoColor=white)
![pgvector](https://img.shields.io/badge/pgvector-Supported-purple?logo=postgresql&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-Headless-00B200?logo=playwright&logoColor=white)
![SentenceTransformers](https://img.shields.io/badge/SentenceTransformers-All--MiniLM--L6--v2-orange)

**A fully automated, production-grade RSS feed scraping pipeline with AI semantic deduplication.**  
Collects articles from news sources, extracts full content (bypassing bot protections), calculates AI embeddings, tracks semantic duplicates, and stores everything in PostgreSQL — running silently in the background every hour via Windows Task Scheduler.

</div>

---

## What It Does

> Every hour, this pipeline automatically visits configured news websites, reads their latest articles, uses advanced extractors to pull the full content, computes an AI mathematical embedding of the text, and checks your database to see if the same story has already been covered by a different source. It then saves everything neatly into a PostgreSQL database — ready for analysis.

No manual effort needed. The system handles collection, extraction, AI deduplication, cleaning, storage, and background automation.

---

## 🚀 Key Features

- **AI Semantic Deduplication**: Uses `all-MiniLM-L6-v2` and PostgreSQL `pgvector` to mathematically compare article content. It successfully detects if two different sites (e.g., Ars Technica and TechCrunch) cover the exact same story and marks them with `is_duplicate=True`, tracking the `duplicate_of_id` and `similarity_score`!
- **Advanced Full-Text Extraction**: Uses `Trafilatura` and `Newspaper4k` for best-in-class article body extraction.
- **Headless Browser Fallback**: Built-in Playwright (Chromium) singleton fallback to scrape dynamic, JavaScript-rendered, or bot-protected sites (e.g., Bloomberg, The Verge).
- **Invisible Automation**: Fully integrated with Windows Task Scheduler (`schtasks`) to run seamlessly in the background without keeping a terminal open.
- **Idempotent DB Storage**: Bulk upserts with `ON CONFLICT (url) DO NOTHING` alongside semantic deduplication guarantees no messy duplicate data.

---

## Architecture

```
RSS Sources → Collector → Extractor (Trafilatura/Newspaper4k/Playwright) → AI Embedder → PostgreSQL (pgvector)
```

---

## Tech Stack

| Component | Library/Tool | Purpose |
|-----------|--------------|---------|
| **RSS Parsing** | `feedparser` | Reads XML feeds from sources |
| **Article Extraction** | `trafilatura` + `newspaper4k` | Extracts full article body, author, publish date |
| **JS/Bot Bypass** | `playwright` (Chromium) | Headless browser for complex/protected sites |
| **AI Embeddings** | `sentence-transformers` | Generates 384-dimensional vector embeddings of text |
| **Database** | `psycopg2` + PostgreSQL | Stores articles and metadata |
| **Vector DB** | `pgvector` | Performs high-speed cosine similarity searches |
| **Automation** | Windows `schtasks` | Runs pipeline invisibly in the background |

---

## Database Schema Highlights

### `articles` Table
- `url` (CITEXT UNIQUE) - Prevents exact URL duplicates
- `full_text` (TEXT) - The full scraped article body
- `content_embedding` (VECTOR(384)) - The mathematical representation of the article
- `is_duplicate` (BOOLEAN) - Flagged if AI determines the story is already covered
- `duplicate_of_id` (INT) - Links to the original article ID
- `similarity_score` (FLOAT) - The cosine similarity score (e.g., 0.96)

---

## Setup & Installation

### Prerequisites
- Python 3.12 or higher
- PostgreSQL 15+ with the `pgvector` extension installed.

### 1. Clone the repository
```bash
git clone https://github.com/SauvirWodehra/RSS-Python-Scraping-System.git
cd RSS-Python-Scraping-System
```

### 2. Install dependencies
```bash
python -m pip install -r requirements.txt
playwright install chromium
```

### 3. Configure environment
Create a `.env` file:
```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=rss_pipeline
DB_USER=postgres
DB_PASSWORD=your_postgres_password_here
VECTOR_SIM_THRESHOLD=0.92
```

### 4. Setup the Database
Initialise the pgvector extension and schemas:
```bash
python -c "from db.connection import init_pool, init_schema; init_pool(); init_schema()"
```

---

## Usage & Automation

### Test a Single Article & AI Deduplication
Want to see the AI deduplication in action? Run this script to test inserting a single article:
```bash
python try_add_article.py --url "https://arstechnica.com/..." --source "Ars Technica"
```

### Run the Background Automaton (Windows)
Set up the pipeline to run silently in the background every 60 minutes:
```bash
# Register the automated task
python setup_cron.py

# Check the status of the automated task
python setup_cron.py --status

# Trigger an immediate background run
python setup_cron.py --run-now

# Remove the automated task
python setup_cron.py --remove
```

### Debugging Tools
- `check_db_dups.py`: See recent articles and all AI-flagged duplicates in the DB.
- `fetch_fresh_urls.py`: Grabs the latest URLs from feeds for testing.
- `test_dedup_e2e.py`: Runs a purely simulated end-to-end semantic deduplication test.
- `find_fresh_pairs.py`: Identifies un-scraped articles across sources to test deduplication.

---

## License

MIT License — free to use, modify, and distribute.

---

## Author

**Sauvir Wodehra**  
[GitHub](https://github.com/SauvirWodehra)
