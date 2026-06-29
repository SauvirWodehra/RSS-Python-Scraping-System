"""
pipeline/article_extractor.py
------------------------------
Stage 2b – Article Extractor

Attempts to extract full article text, author, and publish date from a
URL using Newspaper3k. If Newspaper3k fails or returns empty text, falls
back to the BeautifulSoup scraper in web_scraper.py.

Public API:
    extract_article(raw: dict) -> dict   – enriches the raw article dict
    extract_all(raw_articles: list[dict]) -> list[dict]
"""

import logging
import time
from datetime import datetime, timezone

from config.settings import MAX_TEXT_LENGTH
from pipeline.web_scraper import scrape_url, close_browser, PLAYWRIGHT_FIRST_DOMAINS
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

try:
    import newspaper
    _NEWSPAPER_AVAILABLE = True
except ImportError:
    newspaper = None
    _NEWSPAPER_AVAILABLE = False
    logger.warning(
        "newspaper3k / newspaper4k not installed — "
        "full-text extraction will rely solely on BeautifulSoup."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _newspaper_extract(url: str) -> dict:
    """
    Run Newspaper3k extraction on *url*.

    Returns dict with keys: full_text, author, published_at (may be None/empty).
    """
    if not _NEWSPAPER_AVAILABLE or newspaper is None:
        return {"full_text": "", "author": "", "published_at": None}
    try:
        cfg = newspaper.Config()
        cfg.browser_user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
        cfg.request_timeout = 12
        cfg.fetch_images    = False
        cfg.memoize_articles = False

        article = newspaper.Article(url, config=cfg)
        article.download()
        article.parse()

        # Author: newspaper returns a list
        author = ", ".join(article.authors) if article.authors else ""

        # Publish date
        pub_date = None
        if article.publish_date:
            pub_date = article.publish_date
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)

        return {
            "full_text":   (article.text or "").strip(),
            "author":      author,
            "published_at": pub_date,
        }

    except Exception as exc:
        logger.debug("  ℹ️  Newspaper3k failed for %s: %s", url, exc)
        return {"full_text": "", "author": "", "published_at": None}


# ──────────────────────────────────────────────────────────────────────────────
# Core extractor
# ──────────────────────────────────────────────────────────────────────────────

def extract_article(raw: dict) -> dict:
    """
    Enrich one raw article dict with full text, author, and publish date.

    Strategy:
        1. Try Newspaper3k → if full_text is substantial (>200 chars), use it.
        2. Fallback to BeautifulSoup scraper.
        3. If both fail, keep the RSS summary as the best available text.

    Args:
        raw: dict from rss_collector (url, title, summary, published, author, …)

    Returns:
        Enriched dict ready for data_cleaner.
    """
    url = raw["url"]
    logger.debug("  🔍  Extracting: %s", url[:80])

    enriched = dict(raw)  # copy to avoid mutating input

    # ── Detect Playwright-first domains — skip newspaper3k entirely ───────────
    host = urlparse(url).hostname or ""
    host = host.removeprefix("www.")
    _is_playwright_domain = any(
        host == d or host.endswith("." + d) for d in PLAYWRIGHT_FIRST_DOMAINS
    )

    # ── Newspaper3k attempt (skipped for known-blocked domains) ───────────────
    if _is_playwright_domain:
        logger.debug("  ⏭️  Skipping newspaper3k for Playwright-first domain: %s", host)
        np_result = {"full_text": "", "author": "", "published_at": None}
    else:
        np_result = _newspaper_extract(url)

    full_text  = np_result["full_text"]

    if len(full_text) >= 200:
        logger.debug("  ✅  Newspaper3k succeeded (%d chars)", len(full_text))
    else:
        # ── BeautifulSoup / Playwright fallback ───────────────────────────────
        logger.debug("  🔄  Falling back to scraper for %s", url[:60])
        bs4_text = scrape_url(url)
        if len(bs4_text) > len(full_text):
            full_text = bs4_text

    # ── Author: prefer Newspaper3k result if RSS had none ────────────────────
    if np_result["author"] and not enriched.get("author"):
        enriched["author"] = np_result["author"]

    # ── Publish date: prefer Newspaper3k if RSS gave nothing ─────────────────
    if np_result["published_at"] and not enriched.get("published"):
        enriched["published"] = np_result["published_at"]

    # Normalise published → published_at key expected by DB
    enriched["published_at"] = enriched.pop("published", None)

    # Truncate to DB-safe length
    enriched["full_text"] = full_text[:MAX_TEXT_LENGTH] if full_text else None

    return enriched


def extract_all(raw_articles: list[dict]) -> list[dict]:
    """
    Run article extraction for every item in *raw_articles*.

    Adds a short polite delay between requests to avoid hammering servers.

    Returns:
        List of enriched article dicts.
    """
    logger.info("─" * 60)
    logger.info("📰 Article Extractor starting — %d articles", len(raw_articles))
    logger.info("─" * 60)

    enriched_articles = []
    total = len(raw_articles)

    for idx, raw in enumerate(raw_articles, start=1):
        logger.info("  [%d/%d] %s", idx, total, raw.get("title", "")[:70])
        try:
            article = extract_article(raw)
            enriched_articles.append(article)
        except Exception as exc:
            logger.warning("  ❌  Extraction failed for %s: %s", raw.get("url"), exc)
            # Keep the raw article with empty full_text rather than losing it
            raw["full_text"]   = None
            raw["published_at"] = raw.pop("published", None)
            enriched_articles.append(raw)

        time.sleep(0.3)  # polite delay

    logger.info("📦 Extraction complete: %d articles processed.", len(enriched_articles))

    # Release the shared Playwright browser now that all articles are done
    close_browser()

    return enriched_articles
