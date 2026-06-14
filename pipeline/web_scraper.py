"""
pipeline/web_scraper.py
------------------------
Stage 2a – Raw HTML Fallback Scraper

When Newspaper3k fails to extract an article, this module fetches the
page with requests and extracts paragraph text via BeautifulSoup.

Public API:
    scrape_url(url: str) -> str   – returns extracted text or ""
"""

import logging
import re

import requests
from bs4 import BeautifulSoup

from config.settings import REQUEST_TIMEOUT, REQUEST_USER_AGENT

logger = logging.getLogger(__name__)

# Tags whose text content is almost never article body
_NOISE_TAGS = {
    "script", "style", "noscript", "nav", "footer", "header",
    "aside", "form", "button", "iframe", "svg", "figure",
}

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": REQUEST_USER_AGENT})


# ──────────────────────────────────────────────────────────────────────────────

def _fetch_html(url: str) -> str | None:
    """Download raw HTML for *url*. Returns None on any HTTP/network error."""
    try:
        resp = _SESSION.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.Timeout:
        logger.warning("  ⏱️  Timeout fetching %s", url)
    except requests.exceptions.TooManyRedirects:
        logger.warning("  🔄  Too many redirects: %s", url)
    except requests.exceptions.HTTPError as exc:
        logger.warning("  ❌  HTTP %s for %s", exc.response.status_code, url)
    except requests.exceptions.RequestException as exc:
        logger.warning("  ❌  Request error for %s: %s", url, exc)
    return None


def _extract_text(html: str) -> str:
    """
    Parse HTML with BeautifulSoup and return readable paragraph text.

    Strategy:
    1. Remove noise tags (scripts, nav, footer…).
    2. Find the tag containing the most <p> children (likely the article body).
    3. Concatenate <p> text, filtering very short fragments.
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove noise elements in-place
    for tag in soup(list(_NOISE_TAGS)):
        tag.decompose()

    # Find the richest container
    best_container = soup.body or soup
    max_p = 0
    for div in soup.find_all(["div", "article", "section", "main"]):
        p_count = len(div.find_all("p"))
        if p_count > max_p:
            max_p = p_count
            best_container = div

    paragraphs = best_container.find_all("p")
    texts = []
    for p in paragraphs:
        text = p.get_text(separator=" ").strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) > 40:          # skip single-sentence noise
            texts.append(text)

    return "\n\n".join(texts)


def scrape_url(url: str) -> str:
    """
    Fetch *url* and extract article body text via BeautifulSoup.

    Returns:
        Extracted text string, or "" if fetching/parsing fails.
    """
    logger.debug("  🌐  BS4 scraping: %s", url)
    html = _fetch_html(url)
    if not html:
        return ""
    try:
        text = _extract_text(html)
        logger.debug("  📄  BS4 extracted %d chars from %s", len(text), url)
        return text
    except Exception as exc:
        logger.warning("  ⚠️  BS4 parse error for %s: %s", url, exc)
        return ""
