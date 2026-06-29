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

Vector deduplication:
    bulk_insert_articles() integrates semantic similarity search via pgvector.
    Articles with cosine similarity ≥ VECTOR_SIM_THRESHOLD against any existing
    article embedding are treated as duplicates and skipped automatically.
    Falls back to URL-only deduplication when pgvector is unavailable.
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

    The schema is applied in two passes:
      1. Base schema (always runs) — citext extension, rss_sources, articles
         (without embedding column), pipeline_runs.
      2. Vector schema (optional) — pgvector extension + embedding column +
         IVFFlat index. Skipped gracefully if pgvector is not installed on the
         PostgreSQL server, with a clear warning logged.
    """
    schema_dir = pathlib.Path(__file__).parent

    conn = get_connection()
    try:
        # ── Pass 1: Base schema (always required) ─────────────────────────────
        base_sql = (schema_dir / "schema.sql").read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(base_sql)
        conn.commit()
        logger.info("✅ Base database schema initialised.")

        # ── Pass 2: Vector schema (requires pgvector on PostgreSQL server) ─────
        vector_sql = (schema_dir / "schema_vector.sql").read_text(encoding="utf-8")
        try:
            with conn.cursor() as cur:
                cur.execute(vector_sql)
            conn.commit()
            logger.info("✅ Vector schema (pgvector) initialised — semantic dedup ACTIVE.")
        except Exception as vec_exc:
            conn.rollback()
            logger.warning(
                "⚠️  pgvector extension not available on this PostgreSQL server — "
                "semantic deduplication will be disabled, URL-only dedup in effect. "
                "To enable: install pgvector (https://github.com/pgvector/pgvector). "
                "Detail: %s", vec_exc,
            )

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


def get_all_sources() -> list[dict]:
    """
    Fetch all *active* RSS sources from the rss_sources table.

    This is the runtime source of truth — used instead of the hardcoded
    RSS_FEEDS config list so that sources can be managed directly in the DB
    without code changes.

    Returns:
        List of dicts with keys: id, name, url, category
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, name, url, category
                FROM   rss_sources
                WHERE  is_active = TRUE
                ORDER  BY id
                """
            )
            rows = cur.fetchall()
        sources = [dict(r) for r in rows]
        logger.info("📋 Loaded %d active RSS sources from DB.", len(sources))
        return sources
    except Exception as exc:
        logger.error("Failed to load sources from DB: %s", exc)
        raise
    finally:
        release_connection(conn)


def list_all_sources() -> list[dict]:
    """
    Return ALL sources (active AND inactive) from rss_sources.
    Used by the source manager CLI.
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, name, url, category, is_active, last_fetched_at
                FROM   rss_sources
                ORDER  BY id
                """
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        release_connection(conn)


class SemanticDuplicateError(Exception):
    """
    Raised by add_source() when the candidate source is semantically too
    similar to an existing source in the database.

    Attributes:
        match (dict): {id, name, url, category, similarity} of the duplicate.
    """
    def __init__(self, match: dict):
        self.match = match
        super().__init__(
            f"Semantic duplicate of source #{match['id']} "
            f"'{match['name']}' (similarity={match['similarity']:.4f})"
        )


def _fetch_rss_content(url: str, max_articles: int = 5) -> str:
    """
    Fetch an RSS feed and return a rich description string built from:
      - The feed-level title and description (channel metadata)
      - Titles + summaries of the first `max_articles` entries

    This string is used to build a content-aware embedding so that two RSS
    sources covering the same topic are detected as semantic duplicates even
    when their names and URLs look different.

    Returns an empty string on any network/parse error (graceful fallback).
    """
    try:
        import re
        import feedparser

        parsed = feedparser.parse(
            url,
            request_headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; RSSPipeline/1.0; "
                    "+https://github.com/rss-pipeline)"
                )
            },
        )

        def _strip(text: str | None) -> str:
            if not text:
                return ""
            clean = re.sub(r"<[^>]+>", " ", text)
            return re.sub(r"\s+", " ", clean).strip()

        parts: list[str] = []

        # ── Feed-level metadata (channel description) ─────────────────────────
        feed = parsed.get("feed", {})
        feed_title = _strip(feed.get("title", ""))
        feed_desc  = _strip(feed.get("description") or feed.get("subtitle", ""))

        if feed_title:
            parts.append(f"feed: {feed_title}")
        if feed_desc:
            parts.append(f"about: {feed_desc[:200]}")

        # ── Sample article titles + summaries ─────────────────────────────────
        for entry in parsed.entries[:max_articles]:
            title   = _strip(entry.get("title", ""))
            summary = _strip(
                entry.get("summary", "")
                or entry.get("description", "")
            )
            if title:
                parts.append(f"article: {title}")
            if summary:
                parts.append(summary[:150])

        return " ".join(parts)[:700]   # cap total to stay within embed budget

    except Exception as exc:
        logger.debug("_fetch_rss_content failed for %s: %s", url, exc)
        return ""


def add_source(name: str, url: str, category: str = "General") -> int:
    """
    Insert a new RSS source into rss_sources with two-layer deduplication:

    Layer 1 — Semantic (vector) deduplication:
        Fetches the RSS feed live, extracts the channel description and a
        sample of article titles + summaries, then embeds the combined text.
        This means two sources publishing the *same type of content* are
        detected as duplicates even when their names and URLs differ.
        Falls back to name+category-only embedding when the feed is unreachable.
        Requires pgvector and sentence-transformers; skips gracefully if absent.

    Layer 2 — URL deduplication (always active):
        The UNIQUE constraint on rss_sources.url prevents exact-URL duplicates.

    Returns:
        The new source id (int).

    Raises:
        SemanticDuplicateError: if a semantically similar source already exists.
        Exception:              on any database error.
    """
    from config.settings import VECTOR_SIM_THRESHOLD
    from db.vector_store import (
        is_vector_ready, embed_text,
        find_similar_source, store_source_embedding,
    )

    conn = get_connection()
    try:
        # ── Layer 1: Semantic / vector deduplication ──────────────────────────
        if is_vector_ready():
            # Fetch actual RSS feed content so the embedding reflects what
            # articles this source publishes — not just its name and URL.
            rss_content   = _fetch_rss_content(url)
            candidate_text = (
                f"name: {name} category: {category} {rss_content}"
            ).strip()
            logger.info(
                "  📡 Fetched RSS content for embedding (%d chars): %s…",
                len(rss_content), rss_content[:80],
            )
            embedding = embed_text(candidate_text)

            if embedding is not None:
                duplicate = find_similar_source(
                    embedding, conn, threshold=VECTOR_SIM_THRESHOLD
                )
                if duplicate is not None:
                    raise SemanticDuplicateError(duplicate)
        else:
            embedding = None

        # ── Layer 2: URL deduplication + INSERT ───────────────────────────────
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rss_sources (name, url, category)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (name, url, category),
            )
            new_id = cur.fetchone()[0]

        # Persist source embedding so future adds can deduplicate against it
        if embedding is not None:
            store_source_embedding(new_id, embedding, conn)

        conn.commit()
        logger.info("Added source [%d] %s (%s)", new_id, name, url)
        return new_id

    except SemanticDuplicateError:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        logger.error("Failed to add source: %s", exc)
        raise
    finally:
        release_connection(conn)


def remove_source(source_id: int) -> bool:
    """
    Permanently delete a source row by id.
    Returns True if a row was deleted, False if id not found.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM rss_sources WHERE id = %s", (source_id,))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    except Exception as exc:
        conn.rollback()
        logger.error("Failed to remove source %d: %s", source_id, exc)
        raise
    finally:
        release_connection(conn)


def toggle_source(source_id: int, active: bool) -> bool:
    """
    Enable (active=True) or disable (active=False) a source by id.
    Returns True if the row was found and updated.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE rss_sources SET is_active = %s WHERE id = %s",
                (active, source_id),
            )
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as exc:
        conn.rollback()
        logger.error("Failed to toggle source %d: %s", source_id, exc)
        raise
    finally:
        release_connection(conn)


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
    Bulk-upsert articles into the articles table with two-layer deduplication:

    Layer 1 — Semantic (vector) deduplication:
        Each article is embedded with sentence-transformers. If the cosine
        similarity against any existing embedding exceeds VECTOR_SIM_THRESHOLD
        the article is a semantic duplicate and is skipped before INSERT.
        Requires pgvector extension in PostgreSQL and the pgvector Python package.

    Layer 2 — URL deduplication (always active):
        ON CONFLICT (url) DO NOTHING catches exact-URL duplicates that slip
        through the vector check (e.g. previously inserted without embeddings).

    Returns:
        (inserted_count, skipped_count)
    """
    if not articles:
        return 0, 0

    from config.settings import VECTOR_SIM_THRESHOLD
    from db.vector_store import (
        is_vector_ready, embed_text, find_similar_article, store_embedding,
        embed_content_only, store_content_embedding, find_similar_article_by_content,
    )

    use_vectors = is_vector_ready()
    if use_vectors:
        logger.info("  🧠 Vector deduplication ACTIVE (threshold=%.2f)", VECTOR_SIM_THRESHOLD)
    else:
        logger.info("  ⚠️  Vector deduplication INACTIVE — URL-only dedup in effect.")

    sql_insert = """
        INSERT INTO articles
            (source_id, title, url, author, published_at,
             summary, full_text, word_count, language, is_clean,
             is_duplicate, duplicate_of_id, similarity_score)
        VALUES
            (%(source_id)s, %(title)s, %(url)s, %(author)s, %(published_at)s,
             %(summary)s, %(full_text)s, %(word_count)s, %(language)s, %(is_clean)s,
             %(is_duplicate)s, %(duplicate_of_id)s, %(similarity_score)s)
        ON CONFLICT (url) DO NOTHING
        RETURNING id
    """

    conn = get_connection()
    inserted = 0
    skipped_vector = 0
    skipped_url = 0

    try:
        for article in articles:
            # ── Layer 1: Semantic / vector deduplication ──────────────────────
            if use_vectors:
                # Build a structured, labeled embedding text — mirrors how
                # source embeddings are built ("name: … url: … category: …").
                # Fields checked (in order of semantic importance):
                #   title       — most signal-dense; kept in full
                #   category    — topic domain signal (e.g. "Tech", "Finance")
                #   description — RSS-provided summary / teaser (≤ 400 chars)
                #   content     — scraped full article body (≤ 500 chars)
                # Total budget = 1 024 chars enforced by embed_text().
                _title    = (article.get("title")    or "").strip()
                _category = (article.get("category") or "").strip()
                _desc     = (article.get("summary")  or "").strip()[:400]
                _content  = (article.get("full_text") or "").strip()[:500]

                parts = []
                if _title:    parts.append(f"title: {_title}")
                if _category: parts.append(f"category: {_category}")
                if _desc:     parts.append(f"description: {_desc}")
                if _content:  parts.append(f"content: {_content}")

                text_to_embed = " ".join(parts)
                embedding = embed_text(text_to_embed)

                # ── Content-only dedup (stores duplicates with metadata) ──────
                _content_text = (article.get("full_text") or "").strip()
                dup_info = None
                c_emb    = None

                if _content_text and len(_content_text) >= 50:
                    c_emb = embed_content_only(_content_text)
                    if c_emb is not None:
                        dup_info = find_similar_article_by_content(
                            c_emb, conn, threshold=VECTOR_SIM_THRESHOLD
                        )

                # Set duplicate tracking fields on the article dict
                if dup_info is not None:
                    article["is_duplicate"]     = True
                    article["duplicate_of_id"]  = dup_info["id"]
                    article["similarity_score"] = dup_info["similarity"]
                    logger.debug(
                        "  🔁 Content duplicate of #%d (sim=%.4f): %s",
                        dup_info["id"], dup_info["similarity"],
                        (article.get("title") or "")[:80],
                    )
                else:
                    article["is_duplicate"]     = False
                    article["duplicate_of_id"]  = None
                    article["similarity_score"] = None

                if embedding is not None and dup_info is None:
                    # Only run general embedding dedup for non-content-duplicates
                    duplicate_id = find_similar_article(
                        embedding, conn, threshold=VECTOR_SIM_THRESHOLD
                    )
                    if duplicate_id is not None and not article["is_duplicate"]:
                        # Mark as duplicate of the general-embedding match too
                        article["is_duplicate"]     = True
                        article["duplicate_of_id"]  = duplicate_id
                        article["similarity_score"] = VECTOR_SIM_THRESHOLD  # floor estimate
            else:
                embedding = None
                article["is_duplicate"]     = False
                article["duplicate_of_id"]  = None
                article["similarity_score"] = None

            # ── Layer 2: URL deduplication + actual INSERT ────────────────────
            with conn.cursor() as cur:
                cur.execute(sql_insert, article)
                row = cur.fetchone()

            if row is not None:
                # Article was inserted — persist its embeddings
                new_id = row[0]
                inserted += 1
                if embedding is not None:
                    store_embedding(new_id, embedding, conn)
                # Store content-only embedding (only for originals, not duplicates)
                if use_vectors:
                    _content_text = (article.get("full_text") or "").strip()
                    if _content_text:
                        c_emb_to_store = c_emb if 'c_emb' in dir() and c_emb is not None else embed_content_only(_content_text)
                        if c_emb_to_store is not None:
                            store_content_embedding(new_id, c_emb_to_store, conn)

                if article.get("is_duplicate"):
                    skipped_vector += 1   # count as skipped for stats but still stored
            else:
                # ON CONFLICT triggered — URL duplicate
                skipped_url += 1

            conn.commit()

    except Exception as exc:
        conn.rollback()
        logger.error("Bulk insert failed: %s", exc)
        raise
    finally:
        release_connection(conn)

    total_skipped = skipped_vector + skipped_url
    logger.info(
        "  📊 Insert stats — inserted: %d | skipped (vector): %d | skipped (URL): %d",
        inserted, skipped_vector, skipped_url,
    )
    return inserted, total_skipped


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
