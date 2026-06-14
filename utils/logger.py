"""
utils/logger.py
----------------
Centralised logging configuration for the RSS Scraping Pipeline.

Call setup_logging() once at the start of main.py.
All other modules should use:
    logger = logging.getLogger(__name__)
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

from config.settings import LOG_LEVEL, LOG_FILE


def setup_logging() -> None:
    """
    Configure root logger with:
        • RotatingFileHandler  → logs/pipeline.log (10 MB, 5 backups)
        • StreamHandler        → stdout (coloured where supported)
    """
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # ── File handler (rotating) ───────────────────────────────────────────────
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,   # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level)

    # ── Stream handler (stdout, UTF-8 safe on Windows) ───────────────────────
    import io
    utf8_stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    ) if hasattr(sys.stdout, "buffer") else sys.stdout
    stream_handler = logging.StreamHandler(utf8_stdout)
    stream_handler.setFormatter(fmt)
    stream_handler.setLevel(level)

    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    # Silence noisy third-party loggers
    for noisy in ("urllib3", "newspaper", "apscheduler.executors", "chardet"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.info("✅ Logging initialised — level=%s, file=%s", LOG_LEVEL, LOG_FILE)
