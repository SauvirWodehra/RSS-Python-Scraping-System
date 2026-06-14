"""
pipeline/rss_collector.py
--------------------------
Stage 1 – RSS Collector

Reads every configured RSS feed using feedparser, extracts raw entry
metadata, and returns a list of dicts ready for the next pipeline stage.

Each returned dict has:
    url        : str  – article link
    title      : str  – entry title
    summary    : str  – RSS-provided summary / description
    published  : datetime | None – parsed publish time (UTC-aware)
    author     : str  – entry author if present
    source_id  : int  – FK to rss_sources table
    source_name: str
    category   : str
"""

import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser

from config.settings import RSS_FEEDS, REQUEST_TIMEOUT
from db.connection import update_source_last_fetched

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_date(entry: feedparser.util.FeedParserDict) -> datetime | None:
    """
    Try multiple feedparser date fields and return a UTC-aware datetime.
    Falls back to None if nothing can be parsed.
    """
    # feedparser normalises dates into a struct_time in published_parsed / updated_parsed
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
    # Remove HTML tags
    clean = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


# ──────────────────────────────────────────────────────────────────────────────
# Core collector
# ──────────────────────────────────────────────────────────────────────────────

def collect_feed(feed_cfg: dict, source_id: int) -> list[dict]:
    """
    Parse a single RSS feed and return a list of raw article dicts.

    Args:
        feed_cfg  : dict with keys {name, url, category}
        source_id : int – row id from rss_sources

    Returns:
        List of raw article dicts (may be empty on error).
    """
    url  = feed_cfg["url"]
    name = feed_cfg["name"]
    logger.info("  📡 Fetching feed: %s (%s)", name, url)

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
        link = entry.get("link", "").strip()
        title = _clean_text(entry.get("title", ""))
        if not link or not title:
            continue  # skip entries without a URL or title

        # Prefer content field over summary for richer text
        summary_raw = (
            entry.get("content", [{}])[0].get("value", "")
            or entry.get("summary", "")
            or entry.get("description", "")
        )

        articles.append({
            "url":         link,
            "title":       title,
            "summary":     _clean_text(summary_raw)[:2000],  # cap RSS summary
            "published":   _parse_date(entry),
            "author":      _clean_text(entry.get("author", "")),
            "source_id":   source_id,
            "source_name": name,
            "category":    feed_cfg["category"],
        })

    logger.info("  ✅ %s: %d entries collected.", name, len(articles))
    update_source_last_fetched(source_id)
    return articles


def collect_all_feeds(url_to_id: dict[str, int]) -> list[dict]:
    """
    Iterate all configured RSS feeds and aggregate raw articles.

    Args:
        url_to_id: mapping of feed URL → source_id (from seed_sources())

    Returns:
        Combined list of raw article dicts across all feeds.
    """
    logger.info("=" * 60)
    logger.info("🚀 RSS Collector starting — %d feeds configured", len(RSS_FEEDS))
    logger.info("=" * 60)

    all_articles: list[dict] = []

    for feed_cfg in RSS_FEEDS:
        source_id = url_to_id.get(feed_cfg["url"])
        if source_id is None:
            logger.warning("No source_id for feed %s — skipping.", feed_cfg["name"])
            continue

        articles = collect_feed(feed_cfg, source_id)
        all_articles.extend(articles)
        time.sleep(0.5)  # polite crawl delay between feeds

    logger.info("📦 Total raw articles collected: %d", len(all_articles))
    return all_articles
