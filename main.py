"""
main.py
--------
RSS Feed Scraping Pipeline — Entry Point

Usage:
    # Start the full pipeline (runs once immediately, then every 60 min):
    python main.py

    # Run a single pipeline cycle and exit (useful for testing):
    python main.py --once

    # Show analytics report and exit:
    python main.py --report
"""

import argparse
import logging
import sys

# ── Bootstrap path so sub-packages resolve correctly ─────────────────────────
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger import setup_logging
setup_logging()

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Core pipeline function
# ──────────────────────────────────────────────────────────────────────────────

def run_pipeline() -> dict:
    """
    Execute one full pipeline cycle:
        RSS Collector → Article Extractor → Data Cleaner → DB Insert

    Returns:
        Summary dict with keys: found, inserted, skipped, errors, status
    """
    from db.connection import (
        init_pool, seed_sources, get_all_sources,
        bulk_insert_articles,
        create_pipeline_run, finish_pipeline_run,
    )
    from pipeline.rss_collector    import collect_all_feeds
    from pipeline.article_extractor import extract_all
    from pipeline.data_cleaner     import clean

    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║           RSS PIPELINE — FULL CYCLE STARTING             ║")
    logger.info("╚══════════════════════════════════════════════════════════╝")

    run_id   = create_pipeline_run()
    errors   = 0
    inserted = 0
    skipped  = 0
    found    = 0

    try:
        # ── Stage 0: Seed sources from config (idempotent), then load from DB ───
        seed_sources()              # upsert config → rss_sources table
        sources = get_all_sources() # read active sources from DB (runtime truth)

        # ── Stage 1: Collect RSS feeds in parallel ───────────────────────────
        raw_articles = collect_all_feeds(sources)
        found = len(raw_articles)

        if not raw_articles:
            logger.warning("⚠️  No articles collected — aborting cycle.")
            finish_pipeline_run(run_id, 0, 0, 0, 0, "partial")
            return {"found": 0, "inserted": 0, "skipped": 0, "errors": 0, "status": "partial"}

        # ── Stage 2: Extract full article text ────────────────────────────────
        enriched = extract_all(raw_articles)

        # ── Stage 3: Clean & validate ─────────────────────────────────────────
        clean_articles = clean(enriched)

        # ── Stage 4: Insert into PostgreSQL ──────────────────────────────────
        logger.info("─" * 60)
        logger.info("💾 Inserting %d clean articles into PostgreSQL…", len(clean_articles))
        inserted, skipped = bulk_insert_articles(clean_articles)
        logger.info("  ✅  Inserted: %d | Skipped (dupes): %d", inserted, skipped)

        status = "success"

    except Exception as exc:
        logger.exception("❌ Pipeline cycle failed with unhandled error: %s", exc)
        errors  = 1
        status  = "failed"

    finally:
        finish_pipeline_run(run_id, found, inserted, skipped, errors, status)

    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║  PIPELINE COMPLETE  |  found=%-5d inserted=%-5d        ║", found, inserted)
    logger.info("╚══════════════════════════════════════════════════════════╝\n")

    return {
        "found":    found,
        "inserted": inserted,
        "skipped":  skipped,
        "errors":   errors,
        "status":   status,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Startup & CLI
# ──────────────────────────────────────────────────────────────────────────────

def _init_database() -> None:
    """Initialise DB pool + schema (idempotent)."""
    from db.connection import init_pool, init_schema
    init_pool()
    init_schema()
    logger.info("✅ Database ready.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RSS Feed Scraping Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single pipeline cycle and exit.",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print analytics report and exit.",
    )
    args = parser.parse_args()

    _init_database()

    if args.report:
        from analytics.reporter import generate_report
        generate_report()
        return

    # Always run one immediate cycle first
    run_pipeline()

    if args.once:
        logger.info("--once flag set — exiting after single run.")
        return

    # Start the scheduler for continuous operation
    from scheduler.scheduler import build_scheduler, start_scheduler
    scheduler = build_scheduler()
    start_scheduler(scheduler)   # blocks until Ctrl-C


if __name__ == "__main__":
    main()
