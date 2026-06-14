"""
pipeline/data_cleaner.py
-------------------------
Stage 3 – Data Cleaner

Takes a list of enriched article dicts (from article_extractor), loads
them into a Pandas DataFrame, applies cleaning & validation rules, and
returns a list of clean dicts ready for DB insertion.

Cleaning operations:
    • Strip leading/trailing whitespace from all text fields
    • Decode HTML entities (&amp; → &, etc.)
    • Normalise None / NaN to Python None
    • Remove exact URL duplicates (keep first occurrence)
    • Drop rows missing both full_text AND summary (no useful content)
    • Drop rows with title shorter than 5 characters
    • Compute word_count from full_text (or summary fallback)
    • Detect language via langdetect (default 'en' on failure)
    • Mark is_clean = True for rows that pass all checks
    • Export cleaned DataFrame to CSV in exports/ directory

Public API:
    clean(enriched: list[dict]) -> list[dict]
"""

import logging
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import pandas as pd

from config.settings import EXPORTS_DIR

logger = logging.getLogger(__name__)

# Optional language detection (graceful fallback if not installed)
try:
    from langdetect import detect, LangDetectException
    _LANG_DETECT_AVAILABLE = True
except ImportError:
    _LANG_DETECT_AVAILABLE = False
    logger.warning("langdetect not installed — language will default to 'en'.")


# ──────────────────────────────────────────────────────────────────────────────
# Text-level helpers
# ──────────────────────────────────────────────────────────────────────────────

def _strip_html_entities(text: str | None) -> str:
    """Decode HTML entities and strip surrounding whitespace."""
    if not text:
        return ""
    return unescape(str(text)).strip()


def _collapse_whitespace(text: str) -> str:
    """Replace multiple consecutive whitespace chars with a single space."""
    return re.sub(r"\s+", " ", text).strip()


def _detect_language(text: str) -> str:
    """Detect ISO 639-1 language code. Returns 'en' on any failure."""
    if not _LANG_DETECT_AVAILABLE or not text or len(text) < 50:
        return "en"
    try:
        return detect(text[:2000])
    except Exception:
        return "en"


def _word_count(text: str | None) -> int:
    if not text:
        return 0
    return len(text.split())


# ──────────────────────────────────────────────────────────────────────────────
# DataFrame cleaning pipeline
# ──────────────────────────────────────────────────────────────────────────────

def _clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Apply all cleaning rules to the DataFrame. Returns cleaned DataFrame."""

    original_count = len(df)
    logger.info("  🧹 Starting cleaning — %d rows", original_count)

    # ── 1. Normalise text fields ──────────────────────────────────────────────
    for col in ("title", "summary", "author", "full_text"):
        if col in df.columns:
            df[col] = df[col].apply(_strip_html_entities)
            df[col] = df[col].apply(_collapse_whitespace)
            df[col] = df[col].replace("", None)  # empty string → NULL

    # ── 2. Deduplicate by URL ─────────────────────────────────────────────────
    before_dedup = len(df)
    df = df.drop_duplicates(subset=["url"], keep="first")
    dropped_dupes = before_dedup - len(df)
    if dropped_dupes:
        logger.info("  🗑️  Removed %d duplicate URLs", dropped_dupes)

    # ── 3. Drop rows with no usable text ─────────────────────────────────────
    mask_no_text = df["full_text"].isna() & df["summary"].isna()
    df = df[~mask_no_text]
    dropped_no_text = mask_no_text.sum()
    if dropped_no_text:
        logger.info("  🗑️  Dropped %d rows with no text content", dropped_no_text)

    # ── 4. Drop rows with too-short titles ───────────────────────────────────
    mask_short_title = df["title"].isna() | (df["title"].str.len() < 5)
    df = df[~mask_short_title]

    # ── 5. Normalise published_at ─────────────────────────────────────────────
    def _norm_dt(val):
        if pd.isna(val) or val is None:
            return None
        if isinstance(val, datetime):
            if val.tzinfo is None:
                return val.replace(tzinfo=timezone.utc)
            return val
        return None

    df["published_at"] = df["published_at"].apply(_norm_dt)

    # ── 6. Compute word_count (pandas 3.x safe) ──────────────────────────────
    # Use full_text when available, else summary; avoids loc StringArray→int64
    text_for_wc = df["full_text"].fillna(df["summary"].fillna(""))
    df["word_count"] = text_for_wc.apply(_word_count).astype("int64")

    # ── 7. Detect language ────────────────────────────────────────────────────
    logger.info("  🌐  Detecting languages…")
    text_for_lang = df["full_text"].fillna(df["summary"].fillna(""))
    df["language"] = text_for_lang.apply(_detect_language)

    # ── 8. Mark clean ────────────────────────────────────────────────────────
    df["is_clean"] = True

    # ── 9. Ensure required DB columns exist with defaults ────────────────────
    df["source_id"]    = df.get("source_id", pd.Series([None] * len(df)))
    df["author"]       = df.get("author",    pd.Series([None] * len(df)))
    df["full_text"]    = df.get("full_text",  pd.Series([None] * len(df)))

    # ── 10. Select only DB columns in correct order ───────────────────────────
    db_columns = [
        "source_id", "title", "url", "author",
        "published_at", "summary", "full_text",
        "word_count", "language", "is_clean",
    ]
    # Fill any missing columns
    for col in db_columns:
        if col not in df.columns:
            df[col] = None

    df = df[db_columns].reset_index(drop=True)

    logger.info(
        "  ✅  Cleaning complete — %d rows (removed %d total)",
        len(df), original_count - len(df),
    )
    return df


# ──────────────────────────────────────────────────────────────────────────────
# CSV export
# ──────────────────────────────────────────────────────────────────────────────

def _export_csv(df: pd.DataFrame) -> Path:
    """Write the cleaned DataFrame to a timestamped CSV file in exports/."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath  = EXPORTS_DIR / f"articles_{timestamp}.csv"
    df.to_csv(filepath, index=False, encoding="utf-8-sig")
    logger.info("  💾  CSV exported → %s", filepath)
    return filepath


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def clean(enriched: list[dict]) -> list[dict]:
    """
    Clean a list of enriched article dicts.

    Args:
        enriched: output from article_extractor.extract_all()

    Returns:
        List of cleaned dicts, each matching the articles table schema.
    """
    if not enriched:
        logger.warning("  ⚠️  Data cleaner received empty input.")
        return []

    logger.info("─" * 60)
    logger.info("🧼 Data Cleaner starting — %d articles", len(enriched))
    logger.info("─" * 60)

    df = pd.DataFrame(enriched)

    # Replace pandas NA/NaT with None for psycopg2 compatibility
    df = df.where(pd.notna(df), None)

    df = _clean_dataframe(df)

    # Export to CSV on every run
    _export_csv(df)

    # Convert back to list of plain dicts
    records = df.to_dict(orient="records")

    # Final None normalisation — replace pd.NA / float NaN with None
    cleaned = []
    for rec in records:
        clean_rec = {}
        for k, v in rec.items():
            if v is pd.NA or (isinstance(v, float) and str(v) == "nan"):
                clean_rec[k] = None
            else:
                clean_rec[k] = v
        cleaned.append(clean_rec)

    logger.info("📦 Data Cleaner done — %d clean records ready.", len(cleaned))
    return cleaned
