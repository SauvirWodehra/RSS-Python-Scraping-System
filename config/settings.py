"""
config/settings.py
------------------
Central configuration for the RSS Scraping Pipeline.
All environment-sensitive values are loaded from .env (optional),
with sensible defaults for local development.
"""

import os
from dotenv import load_dotenv

load_dotenv()  # Load .env if present

# ──────────────────────────────────────────────────────────────────────────────
# PostgreSQL Configuration
# ──────────────────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5432)),
    "dbname":   os.getenv("DB_NAME", "rss_pipeline"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),   # Set via .env file — never hardcode
}

# Connection pool size
DB_MIN_CONNECTIONS = 1
DB_MAX_CONNECTIONS = 10

# ──────────────────────────────────────────────────────────────────────────────
# Scheduler Configuration
# ──────────────────────────────────────────────────────────────────────────────
SCHEDULER_INTERVAL_MINUTES = int(os.getenv("SCHEDULER_INTERVAL_MINUTES", 60))

# ──────────────────────────────────────────────────────────────────────────────
# Scraper Configuration
# ──────────────────────────────────────────────────────────────────────────────
REQUEST_TIMEOUT    = 15      # seconds
REQUEST_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
MAX_TEXT_LENGTH = 50_000     # truncate full_text beyond this character limit

# ──────────────────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────────────────
import pathlib
BASE_DIR    = pathlib.Path(__file__).resolve().parent.parent
LOGS_DIR    = BASE_DIR / "logs"
EXPORTS_DIR = BASE_DIR / "exports"

LOGS_DIR.mkdir(exist_ok=True)
EXPORTS_DIR.mkdir(exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE  = LOGS_DIR / "pipeline.log"

# ──────────────────────────────────────────────────────────────────────────────
# Vector Embedding Deduplication
# ──────────────────────────────────────────────────────────────────────────────
# Cosine similarity threshold for semantic deduplication (0.0 – 1.0).
# Articles with similarity ≥ this value are treated as duplicates and skipped.
# Higher = stricter (fewer false positives). Recommended range: 0.90 – 0.95.
VECTOR_SIM_THRESHOLD = float(os.getenv("VECTOR_SIM_THRESHOLD", "0.92"))

# Sentence-transformers model to use for generating embeddings.
# all-MiniLM-L6-v2 → 384 dims, fast, good quality, ~80 MB download.
VECTOR_MODEL_NAME = os.getenv("VECTOR_MODEL_NAME", "all-MiniLM-L6-v2")

# ──────────────────────────────────────────────────────────────────────────────
# RSS Feed Sources
# Format: {"name": str, "url": str, "category": str}
# ──────────────────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    # ── Technology ────────────────────────────────────────────────────────────
    {
        "name": "TechCrunch",
        "url":  "https://techcrunch.com/feed/",
        "category": "Technology",
    },
    {
        "name": "The Verge",
        "url":  "https://www.theverge.com/rss/index.xml",
        "category": "Technology",
    },
    {
        "name": "Wired",
        "url":  "https://www.wired.com/feed/rss",
        "category": "Technology",
    },
    {
        "name": "Ars Technica",
        "url":  "https://feeds.arstechnica.com/arstechnica/index",
        "category": "Technology",
    },
    # ── Finance ───────────────────────────────────────────────────────────────
    {
        "name": "Reuters Business",
        "url":  "https://feeds.reuters.com/reuters/businessNews",
        "category": "Finance",
    },
    {
        "name": "Yahoo Finance",
        "url":  "https://finance.yahoo.com/news/rssindex",
        "category": "Finance",
    },
    {
        "name": "MarketWatch",
        "url":  "https://feeds.marketwatch.com/marketwatch/topstories/",
        "category": "Finance",
    },
    # ── General News ──────────────────────────────────────────────────────────
    {
        "name": "BBC News",
        "url":  "http://feeds.bbci.co.uk/news/rss.xml",
        "category": "General",
    },
    {
        "name": "Reuters Top News",
        "url":  "https://feeds.reuters.com/reuters/topNews",
        "category": "General",
    },
    {
        "name": "NPR News",
        "url":  "https://feeds.npr.org/1001/rss.xml",
        "category": "General",
    },
    # ── Science ───────────────────────────────────────────────────────────────
    {
        "name": "NASA Breaking News",
        "url":  "https://www.nasa.gov/rss/dyn/breaking_news.rss",
        "category": "Science",
    },
    {
        "name": "ScienceDaily",
        "url":  "https://www.sciencedaily.com/rss/all.xml",
        "category": "Science",
    },
    {
        "name": "New Scientist",
        "url":  "https://www.newscientist.com/feed/home/",
        "category": "Science",
    },
]
