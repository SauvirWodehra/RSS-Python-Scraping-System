"""
pipeline/rss_collector.py
--------------------------
Stage 1 – RSS Collector

Reads every active RSS source from the database using feedparser, extracts
raw entry metadata, and returns a list of dicts ready for the next pipeline
stage.

Feeds are fetched in PARALLEL using a ThreadPoolExecutor (network I/O is
the bottleneck; threads give near-linear speedup up to the worker cap).

Each returned dict has:
    url        : str  – article link
    title      : str  – entry title
    summary    : str  – RSS-provided summary / description
    published  : datetime | None – parsed publish time (UTC-aware)
    author     : str  – entry author if present
    source_id  : int  – FK to rss_sources table
    source_name: str
    category   : str

Public API:
    collect_feed(source: dict) -> list[dict]
    collect_all_feeds(sources: list[dict]) -> list[dict]
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser

from config.settings import REQUEST_TIMEOUT
from db.connection import update_source_last_fetched

logger = logging.getLogger(__name__)

# Maximum parallel feed-fetch workers.
# RSS fetching is pure network I/O — 8 workers is polite yet fast.
_MAX_WORKERS = 8

# Thread-safe lock for aggregating results (list.extend is not atomic)
_results_lock = threading.Lock()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_date(entry: feedparser.util.FeedParserDict) -> datetime | None:
    """
    Try multiple feedparser date fields and return a UTC-aware datetime.
    Falls back to None if nothing can be parsed.
    """
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                continue
    # Try raw RFC-2822 string
    for attr in ("published", "updated"):
        raw = entry.get(attr, "")
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc)
            except Exception:
                continue
    return None


def _clean_text(text: str | None) -> str:
    """Strip HTML tags from RSS summary fields."""
    if not text:
        return ""
    import re
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


# ──────────────────────────────────────────────────────────────────────────────
# Core collector — runs inside a worker thread
# ──────────────────────────────────────────────────────────────────────────────

def collect_feed(source: dict) -> list[dict]:
    """
    Parse a single RSS feed and return a list of raw article dicts.

    Thread-safe: feedparser.parse() is stateless; update_source_last_fetched()
    uses psycopg2.ThreadedConnectionPool which is thread-safe.

    Args:
        source: dict with keys {id, name, url, category}  (from get_all_sources())

    Returns:
        List of raw article dicts (may be empty on error).
    """
    source_id = source["id"]
    url       = source["url"]
    name      = source["name"]
    category  = source["category"]

    logger.info("  📡 Fetching feed: %s", name)

    try:
        parsed = feedparser.parse(url, request_headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; RSSPipeline/1.0; "
                "+https://github.com/rss-pipeline)"
            )
        })
    except Exception as exc:
        logger.warning("  ⚠️  feedparser error for %s: %s", name, exc)
        return []

    if parsed.bozo and parsed.bozo_exception:
        logger.warning("  ⚠️  Malformed feed %s: %s", name, parsed.bozo_exception)

    entries = parsed.entries
    if not entries:
        logger.info("  ℹ️  No entries found in %s", name)
        return []

    articles = []
    for entry in entries:
        link  = entry.get("link", "").strip()
        title = _clean_text(entry.get("title", ""))
        if not link or not title:
            continue

        summary_raw = (
            entry.get("content", [{}])[0].get("value", "")
            or entry.get("summary", "")
            or entry.get("description", "")
        )

        articles.append({
            "url":         link,
            "title":       title,
            "summary":     _clean_text(summary_raw)[:2000],
            "published":   _parse_date(entry),
            "author":      _clean_text(entry.get("author", "")),
            "source_id":   source_id,
            "source_name": name,
            "category":    category,
        })

    logger.info("  ✅ %-20s %3d entries", name, len(articles))
    update_source_last_fetched(source_id)
    return articles


# ──────────────────────────────────────────────────────────────────────────────
# Parallel orchestrator
# ──────────────────────────────────────────────────────────────────────────────

def collect_all_feeds(sources: list[dict]) -> list[dict]:
    """
    Fetch all RSS feeds in PARALLEL and aggregate raw articles.

    Sources come from the database (get_all_sources()), not the config file.
    A ThreadPoolExecutor is used because RSS fetching is pure network I/O —
    multiple feeds can be downloaded simultaneously with no GIL contention.

    Args:
        sources: list of dicts {id, name, url, category} — from get_all_sources()

    Returns:
        Combined list of raw article dicts across all feeds.
    """
    n_workers = min(_MAX_WORKERS, len(sources))

    logger.info("=" * 60)
    logger.info(
        "🚀 RSS Collector — %d active sources from DB | %d parallel workers",
        len(sources), n_workers,
    )
    logger.info("=" * 60)

    all_articles: list[dict] = []
    errors: int = 0

    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        # Submit all feed-fetch tasks at once
        future_to_source = {
            executor.submit(collect_feed, source): source
            for source in sources
        }

        # Collect results as each future completes (order varies)
        for future in as_completed(future_to_source):
            source = future_to_source[future]
            try:
                articles = future.result()
                with _results_lock:
                    all_articles.extend(articles)
            except Exception as exc:
                errors += 1
                logger.warning(
                    "  ❌  Feed collection failed for %s: %s",
                    source["name"], exc,
                )

    logger.info("─" * 60)
    logger.info(
        "📦 Collection complete — %d articles from %d feeds (%d errors)",
        len(all_articles), len(sources), errors,
    )
    return all_articles
