"""
db/connection.py
----------------
PostgreSQL connection pool management using psycopg2.

Usage:
    from db.connection import get_connection, release_connection, init_schema, seed_sources

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    finally:
        release_connection(conn)
"""

import logging
import pathlib
import psycopg2
from psycopg2 import pool, extras
from config.settings import DB_CONFIG, DB_MIN_CONNECTIONS, DB_MAX_CONNECTIONS, RSS_FEEDS

logger = logging.getLogger(__name__)

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


# ──────────────────────────────────────────────────────────────────────────────
# Pool initialisation
# ──────────────────────────────────────────────────────────────────────────────

def init_pool() -> None:
    """Create the global threaded connection pool. Called once at startup."""
    global _pool
    if _pool is not None:
        return
    try:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=DB_MIN_CONNECTIONS,
            maxconn=DB_MAX_CONNECTIONS,
            **DB_CONFIG,
        )
        logger.info("✅ PostgreSQL connection pool initialised (min=%d, max=%d)",
                    DB_MIN_CONNECTIONS, DB_MAX_CONNECTIONS)
    except psycopg2.OperationalError as exc:
        logger.critical("❌ Cannot connect to PostgreSQL: %s", exc)
        raise


def get_connection() -> psycopg2.extensions.connection:
    """Borrow a connection from the pool."""
    if _pool is None:
        init_pool()
    return _pool.getconn()


def release_connection(conn: psycopg2.extensions.connection) -> None:
    """Return a connection to the pool."""
    if _pool and conn:
        _pool.putconn(conn)


def close_pool() -> None:
    """Close all connections in the pool (call on shutdown)."""
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None
        logger.info("PostgreSQL pool closed.")


# ──────────────────────────────────────────────────────────────────────────────
# Schema initialisation
# ──────────────────────────────────────────────────────────────────────────────

def init_schema() -> None:
    """
    Execute db/schema.sql against the database.
    Idempotent — uses CREATE TABLE IF NOT EXISTS everywhere.
    """
    schema_path = pathlib.Path(__file__).parent / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        logger.info("✅ Database schema initialised.")
    except Exception as exc:
        conn.rollback()
        logger.error("Schema initialisation failed: %s", exc)
        raise
    finally:
        release_connection(conn)


# ──────────────────────────────────────────────────────────────────────────────
# Source seeding
# ──────────────────────────────────────────────────────────────────────────────

def seed_sources() -> dict[str, int]:
    """
    Upsert all RSS sources from settings into rss_sources table.

    Returns:
        dict mapping feed URL → source_id (int)
    """
    conn = get_connection()
    url_to_id: dict[str, int] = {}
    try:
        with conn.cursor() as cur:
            for feed in RSS_FEEDS:
                cur.execute(
                    """
                    INSERT INTO rss_sources (name, url, category)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (url) DO UPDATE
                        SET name     = EXCLUDED.name,
                            category = EXCLUDED.category
                    RETURNING id
                    """,
                    (feed["name"], feed["url"], feed["category"]),
                )
                row = cur.fetchone()
                url_to_id[feed["url"]] = row[0]
        conn.commit()
        logger.info("✅ Seeded %d RSS sources.", len(url_to_id))
    except Exception as exc:
        conn.rollback()
        logger.error("Source seeding failed: %s", exc)
        raise
    finally:
        release_connection(conn)
    return url_to_id


# ──────────────────────────────────────────────────────────────────────────────
# Generic helpers
# ──────────────────────────────────────────────────────────────────────────────

def execute_query(sql: str, params: tuple = (), fetch: bool = False):
    """
    Execute a single SQL statement with auto-commit.
    Optionally returns fetched rows (list of RealDictRow).
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            conn.commit()
            if fetch:
                return cur.fetchall()
    except Exception as exc:
        conn.rollback()
        logger.error("Query failed [%s]: %s", sql[:80], exc)
        raise
    finally:
        release_connection(conn)


def bulk_insert_articles(articles: list[dict]) -> tuple[int, int]:
    """
    Bulk-upsert articles into the articles table.
    Skips duplicates (ON CONFLICT DO NOTHING on url).

    Returns:
        (inserted_count, skipped_count)
    """
    if not articles:
        return 0, 0

    sql = """
        INSERT INTO articles
            (source_id, title, url, author, published_at,
             summary, full_text, word_count, language, is_clean)
        VALUES
            (%(source_id)s, %(title)s, %(url)s, %(author)s, %(published_at)s,
             %(summary)s, %(full_text)s, %(word_count)s, %(language)s, %(is_clean)s)
        ON CONFLICT (url) DO NOTHING
    """
    conn = get_connection()
    inserted = 0
    try:
        with conn.cursor() as cur:
            for article in articles:
                cur.execute(sql, article)
                inserted += cur.rowcount
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.error("Bulk insert failed: %s", exc)
        raise
    finally:
        release_connection(conn)

    skipped = len(articles) - inserted
    return inserted, skipped


def update_source_last_fetched(source_id: int) -> None:
    """Mark an RSS source as just fetched."""
    execute_query(
        "UPDATE rss_sources SET last_fetched_at = NOW() WHERE id = %s",
        (source_id,),
    )


def create_pipeline_run() -> int:
    """Insert a new pipeline_runs row and return its id."""
    rows = execute_query(
        "INSERT INTO pipeline_runs DEFAULT VALUES RETURNING id",
        fetch=True,
    )
    return rows[0]["id"]


def finish_pipeline_run(run_id: int, found: int, inserted: int,
                         skipped: int, errors: int, status: str) -> None:
    """Update a pipeline_runs row on completion."""
    execute_query(
        """
        UPDATE pipeline_runs
           SET finished_at       = NOW(),
               articles_found    = %s,
               articles_inserted = %s,
               articles_skipped  = %s,
               errors            = %s,
               status            = %s
         WHERE id = %s
        """,
        (found, inserted, skipped, errors, status, run_id),
    )
