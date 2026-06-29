"""
pipeline/web_scraper.py
------------------------
Stage 2a – Raw HTML Fallback Scraper

Extraction tiers (in order):
    1. requests + BeautifulSoup      — fast path for open/static pages.
    2. Playwright (headless Chromium) — for JS-rendered / bot-protected pages.

Domains listed in PLAYWRIGHT_FIRST_DOMAINS skip Tier 1 entirely and go
straight to Playwright, avoiding wasted 406/403 round-trips.

Playwright uses a *persistent singleton browser* — Chromium is launched
once per pipeline run and reused for all articles, which is much faster
and looks less like a bot than opening a fresh browser every request.

Public API:
    scrape_url(url: str) -> str   – returns extracted text or ""
    close_browser()               – release Playwright resources; call at pipeline end
"""

import atexit
import logging
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from config.settings import REQUEST_TIMEOUT, REQUEST_USER_AGENT

logger = logging.getLogger(__name__)

# ── Noise tags — almost never article body ─────────────────────────────────────
_NOISE_TAGS = {
    "script", "style", "noscript", "nav", "footer", "header",
    "aside", "form", "button", "iframe", "svg", "figure",
}

# ── Domains that block plain requests (403/406/Cloudflare/JS-wall) ─────────────
# For these, skip Tier 1 and go straight to Playwright.
PLAYWRIGHT_FIRST_DOMAINS = {
    "newscientist.com",
    "wired.com",
    "theverge.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "nytimes.com",
    "thetimes.co.uk",
    "telegraph.co.uk",
}

# ── requests session ───────────────────────────────────────────────────────────
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": REQUEST_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
})

# ── Playwright availability ────────────────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False
    logger.warning(
        "playwright not installed — Playwright fallback disabled. "
        "Run: pip install playwright && playwright install chromium"
    )

# ── Playwright persistent browser singleton ────────────────────────────────────
_pw_instance = None
_pw_browser  = None


def _get_browser():
    """
    Lazily initialize and return the shared Playwright Chromium browser.

    The browser is started once and reused across all scrape_url() calls,
    which is both faster (no cold-start overhead) and more realistic
    (consistent browser fingerprint across the session).
    """
    global _pw_instance, _pw_browser
    if not _PLAYWRIGHT_AVAILABLE:
        return None
    if _pw_browser is None:
        logger.info("  🚀  Starting persistent Playwright browser…")
        _pw_instance = sync_playwright().start()
        _pw_browser  = _pw_instance.chromium.launch(
            headless=True,
            args=[
                # Disable the 'navigator.webdriver' flag that reveals automation
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
                "--lang=en-US,en",
            ],
        )
        logger.info("  ✅  Playwright browser ready.")
    return _pw_browser


def close_browser() -> None:
    """
    Cleanly shut down the persistent Playwright browser and stop the
    Playwright driver.  Call this once at the end of a pipeline run.
    """
    global _pw_instance, _pw_browser
    if _pw_browser is not None:
        try:
            _pw_browser.close()
            logger.info("  🛑  Playwright browser closed.")
        except Exception:
            pass
        _pw_browser = None
    if _pw_instance is not None:
        try:
            _pw_instance.stop()
        except Exception:
            pass
        _pw_instance = None


# Register close_browser() as a Python atexit handler so the browser is
# always cleaned up even if the pipeline exits unexpectedly.
atexit.register(close_browser)


# ── Domain helper ──────────────────────────────────────────────────────────────

def _domain(url: str) -> str:
    """Return the registered domain (e.g. 'newscientist.com') of *url*."""
    host = urlparse(url).hostname or ""
    # Strip common 'www.' prefix
    return host.removeprefix("www.")


def _needs_playwright_first(url: str) -> bool:
    """Return True if *url*'s domain is in the Playwright-first list."""
    dom = _domain(url)
    return any(dom == d or dom.endswith("." + d) for d in PLAYWRIGHT_FIRST_DOMAINS)


# ──────────────────────────────────────────────────────────────────────────────
# Tier 1: requests
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_html_requests(url: str) -> str | None:
    """Download raw HTML via requests. Returns None on any HTTP/network error."""
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


# ──────────────────────────────────────────────────────────────────────────────
# Tier 2: Playwright headless browser
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_html_playwright(url: str) -> str | None:
    """
    Fetch *url* with the shared Playwright Chromium browser.

    Anti-bot measures applied per context:
    • Realistic viewport, locale, timezone
    • Overridden navigator.webdriver → false
    • Extra HTTP headers mimicking a real Chrome browser
    • Blocks images / media / fonts (speed) but NOT JS or XHR

    Wait strategy: 'domcontentloaded' (fast, ~2-4 s) + 1.5 s JS settle
    instead of 'networkidle' (can block for 20 s+).  If page.content()
    raises a "still navigating" error we wait for 'load' and retry once.
    """
    browser = _get_browser()
    if browser is None:
        return None

    logger.info("  🎭  Playwright fetching: %s", url[:90])
    try:
        context = browser.new_context(
            user_agent=REQUEST_USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            },
        )

        # Override JS navigator.webdriver flag → false
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = context.new_page()

        # Block heavy/unnecessary resource types for speed
        def _route_handler(route):
            if route.request.resource_type in ("image", "media", "font", "stylesheet"):
                route.abort()
            else:
                route.continue_()

        page.route("**/*", _route_handler)

        # domcontentloaded is much faster than networkidle;
        # the extra 1 500 ms gives JS frameworks time to render the body.
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(1_500)   # brief JS settle
        except PWTimeoutError:
            logger.debug(
                "  ⏱️  Playwright domcontentloaded timeout — reading partial DOM: %s", url
            )

        # page.content() can fail if a redirect fires right as we call it.
        # Retry once after waiting for the 'load' event.
        html = None
        for attempt in range(2):
            try:
                html = page.content()
                break
            except Exception as content_exc:
                if attempt == 0:
                    logger.debug(
                        "  🔁  page.content() still navigating — waiting for load: %s", url
                    )
                    try:
                        page.wait_for_load_state("load", timeout=8_000)
                    except Exception:
                        pass
                else:
                    logger.warning(
                        "  ❌  page.content() failed after retry for %s: %s", url, content_exc
                    )

        context.close()
        return html

    except Exception as exc:
        logger.warning("  ❌  Playwright error for %s: %s", url, exc)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# HTML → text extraction (shared by both tiers)
# ──────────────────────────────────────────────────────────────────────────────

def _extract_text(html: str) -> str:
    """
    Parse HTML and return readable paragraph text.

    Strategy:
    1. Try trafilatura (best-in-class article extraction).
    2. Fallback to BeautifulSoup (remove noise tags, find richest <p> container).
    """
    try:
        import trafilatura
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        if text and len(text) > 100:
            return text.strip()
    except Exception:
        pass

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


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

# Minimum chars to consider a tier's result "good enough"
_MIN_TEXT_CHARS = 200


def scrape_url(url: str) -> str:
    """
    Fetch *url* and extract article body text.

    Extraction strategy:
        - Playwright-first domains  → skip requests, go straight to Playwright.
        - All other domains         → try requests first; escalate to Playwright
                                      if result is thin (< _MIN_TEXT_CHARS chars).

    Returns:
        Extracted text string, or "" if all tiers fail.
    """
    text = ""

    if _needs_playwright_first(url):
        # ── Playwright-first path ─────────────────────────────────────────────
        logger.info("  🎭  Playwright-first domain — skipping requests: %s", _domain(url))
        pw_html = _fetch_html_playwright(url)
        if pw_html:
            try:
                text = _extract_text(pw_html)
                logger.info("  📄  Playwright extracted %d chars from %s", len(text), url[:80])
            except Exception as exc:
                logger.warning("  ⚠️  Playwright BS4 parse error for %s: %s", url, exc)
        return text

    # ── Tier 1: requests + BeautifulSoup ─────────────────────────────────────
    logger.debug("  🌐  Tier-1 (requests) scraping: %s", url)
    html = _fetch_html_requests(url)
    if html:
        try:
            text = _extract_text(html)
            logger.debug("  📄  Tier-1 extracted %d chars from %s", len(text), url)
        except Exception as exc:
            logger.warning("  ⚠️  Tier-1 BS4 parse error for %s: %s", url, exc)

    if len(text) >= _MIN_TEXT_CHARS:
        return text

    # ── Tier 2: Playwright escalation ────────────────────────────────────────
    if _PLAYWRIGHT_AVAILABLE:
        logger.info(
            "  🎭  Tier-1 thin (%d chars) — escalating to Playwright: %s",
            len(text), url[:80],
        )
        pw_html = _fetch_html_playwright(url)
        if pw_html:
            try:
                pw_text = _extract_text(pw_html)
                logger.info("  📄  Playwright extracted %d chars from %s", len(pw_text), url[:80])
                if len(pw_text) > len(text):
                    return pw_text
            except Exception as exc:
                logger.warning("  ⚠️  Playwright BS4 parse error for %s: %s", url, exc)

    return text
