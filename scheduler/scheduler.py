"""
scheduler/scheduler.py
-----------------------
APScheduler-based job orchestrator.

Registers `run_pipeline` as an interval job (default: every 60 minutes)
and keeps the process alive until Ctrl-C or SIGTERM.

The scheduler is also exported so that main.py can start/stop it cleanly.
"""

import logging
import signal
import sys

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from config.settings import SCHEDULER_INTERVAL_MINUTES

logger = logging.getLogger(__name__)

# Lazy import to avoid circular dependency at module load time
_run_pipeline_fn = None


def _job_listener(event):
    """Log APScheduler job execution results."""
    if event.exception:
        logger.error("💥 Scheduler job FAILED: %s", event.exception)
    else:
        logger.info("⏰ Scheduled pipeline run completed successfully.")


# ──────────────────────────────────────────────────────────────────────────────

def _pipeline_job():
    """Wrapper called by APScheduler — imports and runs the pipeline."""
    global _run_pipeline_fn
    if _run_pipeline_fn is None:
        from main import run_pipeline          # noqa: PLC0415
        _run_pipeline_fn = run_pipeline
    logger.info("⏰ Scheduler triggering pipeline run…")
    _run_pipeline_fn()


def build_scheduler() -> BackgroundScheduler:
    """
    Create and configure the APScheduler BackgroundScheduler.

    Returns:
        A configured (but not yet started) BackgroundScheduler instance.
    """
    scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={
            "coalesce":       True,   # if job misfires, run only once
            "max_instances":  1,      # prevent concurrent runs
            "misfire_grace_time": 300,
        },
    )

    scheduler.add_job(
        func=_pipeline_job,
        trigger=IntervalTrigger(minutes=SCHEDULER_INTERVAL_MINUTES),
        id="rss_pipeline",
        name="RSS Scraping Pipeline",
        replace_existing=True,
    )

    scheduler.add_listener(_job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

    return scheduler


def start_scheduler(scheduler: BackgroundScheduler) -> None:
    """
    Start the scheduler and block the main thread until SIGINT/SIGTERM.
    Registers OS signal handlers for clean shutdown.
    """
    def _shutdown(signum, frame):
        logger.info("\n🛑  Shutdown signal received — stopping scheduler…")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    scheduler.start()
    logger.info(
        "✅  Scheduler started — pipeline runs every %d minute(s). "
        "Press Ctrl-C to stop.",
        SCHEDULER_INTERVAL_MINUTES,
    )

    # Block forever — scheduler runs in background threads
    try:
        import time
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑  Keyboard interrupt — shutting down.")
        scheduler.shutdown(wait=True)
