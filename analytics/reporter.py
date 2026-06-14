"""
analytics/reporter.py
----------------------
Analytics & Reporting

Queries the PostgreSQL `articles` table and produces:
    • Console summary table (printed to stdout)
    • CSV report in exports/report_<timestamp>.csv

Can be run standalone:
    python -m analytics.reporter

Or imported and called from other modules:
    from analytics.reporter import generate_report
    generate_report()
"""

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from config.settings import EXPORTS_DIR
from db.connection import get_connection, release_connection

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Query helpers
# ──────────────────────────────────────────────────────────────────────────────

def _query_df(sql: str, params: tuple | dict = ()) -> pd.DataFrame:
    """
    Execute a SELECT query and return results as a Pandas DataFrame.
    Uses cursor-based fetching for full pandas 3.x / psycopg2 compatibility.
    """
    conn = get_connection()
    try:
        from psycopg2 import extras
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(sql, params if params else None)
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(r) for r in rows])
    except Exception as exc:
        logger.error("Analytics query failed: %s", exc)
        return pd.DataFrame()
    finally:
        release_connection(conn)


# ──────────────────────────────────────────────────────────────────────────────
# Report sections
# ──────────────────────────────────────────────────────────────────────────────

def _overall_stats() -> pd.DataFrame:
    return _query_df("""
        SELECT
            COUNT(*)                                     AS total_articles,
            COUNT(DISTINCT source_id)                    AS active_sources,
            COUNT(*) FILTER (WHERE is_clean)             AS clean_articles,
            AVG(word_count)::INT                         AS avg_word_count,
            MIN(published_at)::DATE                      AS earliest_article,
            MAX(published_at)::DATE                      AS latest_article,
            MAX(scraped_at)::TIMESTAMPTZ                 AS last_scraped_at
        FROM articles
    """)


def _articles_per_source() -> pd.DataFrame:
    return _query_df("""
        SELECT
            s.name          AS source,
            s.category,
            COUNT(a.id)     AS article_count,
            AVG(a.word_count)::INT AS avg_words,
            MAX(a.scraped_at)::DATE AS last_scraped
        FROM rss_sources s
        LEFT JOIN articles a ON a.source_id = s.id
        GROUP BY s.name, s.category
        ORDER BY article_count DESC
    """)


def _articles_per_day() -> pd.DataFrame:
    return _query_df("""
        SELECT
            published_at::DATE AS date,
            COUNT(*)           AS articles
        FROM articles
        WHERE published_at IS NOT NULL
        GROUP BY published_at::DATE
        ORDER BY date DESC
        LIMIT 30
    """)


def _top_authors(n: int = 10) -> pd.DataFrame:
    return _query_df("""
        SELECT
            author,
            COUNT(*) AS articles
        FROM articles
        WHERE author IS NOT NULL AND author <> ''
        GROUP BY author
        ORDER BY articles DESC
        LIMIT %s
    """, params=(n,))


def _language_breakdown() -> pd.DataFrame:
    return _query_df("""
        SELECT
            language,
            COUNT(*) AS articles,
            ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS pct
        FROM articles
        GROUP BY language
        ORDER BY articles DESC
    """)


def _pipeline_run_history(limit: int = 10) -> pd.DataFrame:
    return _query_df("""
        SELECT
            id,
            started_at,
            finished_at,
            articles_found,
            articles_inserted,
            articles_skipped,
            errors,
            status
        FROM pipeline_runs
        ORDER BY started_at DESC
        LIMIT %s
    """, params=(limit,))


# ──────────────────────────────────────────────────────────────────────────────
# Report assembly & export
# ──────────────────────────────────────────────────────────────────────────────

def _print_section(title: str, df: pd.DataFrame) -> None:
    """Pretty-print a DataFrame section to stdout."""
    border = "-" * 60
    print(f"\n{border}")
    print(f"  {title}")
    print(border)
    if df.empty:
        print("  (no data)")
    else:
        print(df.to_string(index=False))


def generate_report(export_csv: bool = True) -> dict[str, pd.DataFrame]:
    """
    Build and print the full analytics report.

    Args:
        export_csv: if True, write each section to a multi-sheet–style CSV.

    Returns:
        Dict of section_name → DataFrame.
    """
    logger.info("📊 Generating analytics report…")

    sections = {
        "Overall Stats":        _overall_stats(),
        "Articles per Source":  _articles_per_source(),
        "Articles per Day":     _articles_per_day(),
        "Top Authors":          _top_authors(),
        "Language Breakdown":   _language_breakdown(),
        "Pipeline Run History": _pipeline_run_history(),
    }

    divider = "=" * 60
    print("\n" + divider)
    print("  RSS PIPELINE -- ANALYTICS REPORT")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(divider)

    for title, df in sections.items():
        _print_section(title, df)

    print("\n" + "-" * 60 + "\n")

    if export_csv:
        _export_report_csv(sections)

    return sections


def _export_report_csv(sections: dict[str, pd.DataFrame]) -> None:
    """Export all report sections into a single timestamped CSV."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath  = EXPORTS_DIR / f"report_{timestamp}.csv"

    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        for title, df in sections.items():
            f.write(f"### {title}\n")
            if not df.empty:
                df.to_csv(f, index=False)
            f.write("\n")

    logger.info("💾  Report exported → %s", filepath)
    print(f"\n  Report saved to: {filepath}\n")


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from db.connection import init_pool
    from utils.logger import setup_logging
    setup_logging()
    init_pool()
    generate_report()
