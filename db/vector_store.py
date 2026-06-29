"""
db/vector_store.py
------------------
Vector embedding utilities for semantic article deduplication.

Uses sentence-transformers (all-MiniLM-L6-v2, 384 dims) to generate
dense text embeddings locally (no API key required) and stores them in
PostgreSQL via the pgvector extension.

Public API:
    is_vector_ready() -> bool
        Returns True if pgvector is installed and the model is loaded.

    embed_text(text: str) -> list[float] | None
        Generates a 384-dim embedding for the given text.
        Returns None if the model is unavailable.

    find_similar_article(embedding, conn, threshold) -> int | None
        Cosine-similarity search against existing article embeddings.
        Returns the article_id of the nearest match if similarity ≥ threshold,
        otherwise None (meaning: not a duplicate — safe to insert).

    store_embedding(article_id: int, embedding: list[float], conn) -> None
        Persists the embedding for a newly inserted article.

Graceful fallback:
    If sentence-transformers or pgvector is unavailable, all public
    functions return safe defaults (None / False) so the pipeline
    continues with URL-only deduplication without raising exceptions.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Optional imports — graceful degradation when packages are absent
# ──────────────────────────────────────────────────────────────────────────────

_SENTENCE_TRANSFORMERS_AVAILABLE = False
_PGVECTOR_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    _SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    logger.warning(
        "sentence-transformers not installed — "
        "vector deduplication disabled. "
        "Install with: pip install sentence-transformers"
    )

try:
    from pgvector.psycopg2 import register_vector
    _PGVECTOR_AVAILABLE = True
except ImportError:
    logger.warning(
        "pgvector Python adapter not installed — "
        "vector deduplication disabled. "
        "Install with: pip install pgvector"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Singleton embedding model (lazy-loaded, thread-safe)
# ──────────────────────────────────────────────────────────────────────────────

_model: Optional["SentenceTransformer"] = None
_model_lock = threading.Lock()
_model_load_failed = False   # don't retry a failed load on every article


def _get_model() -> Optional["SentenceTransformer"]:
    """
    Lazily load and cache the SentenceTransformer model.
    Thread-safe via a module-level lock.
    Returns None on any failure.
    """
    global _model, _model_load_failed

    if not _SENTENCE_TRANSFORMERS_AVAILABLE:
        return None

    if _model_load_failed:
        return None

    if _model is not None:
        return _model

    with _model_lock:
        # Double-checked locking
        if _model is not None:
            return _model

        try:
            from config.settings import VECTOR_MODEL_NAME
            logger.info("🔄 Loading embedding model '%s' …", VECTOR_MODEL_NAME)
            _model = SentenceTransformer(VECTOR_MODEL_NAME)
            # get_embedding_dimension() is the new name in sentence-transformers ≥ 3.x
            # fall back to the legacy name for older installs
            get_dim = getattr(
                _model,
                "get_embedding_dimension",
                getattr(_model, "get_sentence_embedding_dimension", lambda: "?"),
            )
            logger.info("✅ Embedding model loaded  (dims=%s)", get_dim())
        except Exception as exc:
            logger.error("❌ Failed to load embedding model: %s — vector dedup disabled.", exc)
            _model_load_failed = True

    return _model


# ──────────────────────────────────────────────────────────────────────────────
# pgvector registration helper
# ──────────────────────────────────────────────────────────────────────────────

_registered_conns: set[int] = set()   # track conn ids that have been registered
_reg_lock = threading.Lock()


def _ensure_vector_registered(conn) -> bool:
    """
    Call pgvector's register_vector(conn) once per connection so psycopg2
    knows how to serialise/deserialise VECTOR columns.
    Returns False if pgvector adapter is unavailable.
    """
    if not _PGVECTOR_AVAILABLE:
        return False

    conn_id = id(conn)
    if conn_id in _registered_conns:
        return True

    with _reg_lock:
        if conn_id in _registered_conns:
            return True
        try:
            register_vector(conn)
            _registered_conns.add(conn_id)
            return True
        except Exception as exc:
            logger.warning("pgvector registration failed: %s", exc)
            return False


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def is_vector_ready() -> bool:
    """
    Returns True only when both the embedding model AND the pgvector
    adapter are available and the model loaded successfully.
    """
    return _PGVECTOR_AVAILABLE and _get_model() is not None


def embed_text(text: str) -> Optional[list[float]]:
    """
    Generate a 384-dim embedding for *text*.

    Args:
        text: The text to embed.  For articles this is a structured string of
              labeled fields:
                "title: <title> category: <cat> description: <summary> content: <full_text>"
              For sources it is:
                "name: <name> url: <url> category: <cat>"
              The total is capped at 1 024 chars inside this function.

    Returns:
        A list of 384 floats, or None if the model is unavailable.
    """
    if not text or not text.strip():
        return None

    model = _get_model()
    if model is None:
        return None

    try:
        vec = model.encode(text[:1024], normalize_embeddings=True)
        return vec.tolist()
    except Exception as exc:
        logger.warning("Embedding generation failed: %s", exc)
        return None


def find_similar_article(
    embedding: list[float],
    conn,
    threshold: float = 0.92,
) -> Optional[int]:
    """
    Search for the most similar article already in the DB.

    Uses pgvector's cosine distance operator (<=>) — distance = 1 - cosine_sim,
    so similarity ≥ threshold  ⟺  distance ≤ (1 - threshold).

    Args:
        embedding:  384-dim vector from embed_text().
        conn:       Active psycopg2 connection (borrowed from pool).
        threshold:  Cosine similarity threshold (0.0–1.0). Default 0.92.

    Returns:
        article_id (int) of the nearest match if it is a duplicate, else None.
    """
    if not _ensure_vector_registered(conn):
        return None

    import numpy as np
    distance_threshold = 1.0 - threshold  # cosine distance equivalent

    try:
        with conn.cursor() as cur:
            # Disable index scan for this query so it always works even when
            # the table has fewer rows than the IVFFlat index's `lists` value
            # (100 by default). The IVFFlat index is approximate and returns
            # zero results on tiny tables; a sequential scan is exact and fast.
            cur.execute("SET LOCAL enable_indexscan = off")
            cur.execute(
                """
                SELECT id, embedding <=> %s::vector AS distance
                FROM   articles
                WHERE  embedding IS NOT NULL
                ORDER  BY embedding <=> %s::vector
                LIMIT  1
                """,
                (embedding, embedding),
            )
            row = cur.fetchone()

        if row is None:
            return None   # no articles with embeddings yet

        article_id, distance = row
        similarity = 1.0 - float(distance)

        if similarity >= threshold:
            logger.debug(
                "  🔁 Semantic duplicate detected (sim=%.4f ≥ %.2f) → article_id=%d",
                similarity, threshold, article_id,
            )
            return article_id

        return None

    except Exception as exc:
        logger.warning("Similarity search failed (falling back to URL dedup): %s", exc)
        return None


def embed_content_only(content: str) -> Optional[list[float]]:
    """
    Generate a 384-dim embedding for article *content* only.

    This is used for content-only semantic deduplication — the embedding
    captures ONLY the article body text, ignoring title, category, URL,
    source name, and any other metadata.  Two articles from completely
    different sources covering the same story will produce near-identical
    embeddings.

    Args:
        content: The full article body text (full_text / content field).

    Returns:
        A list of 384 floats, or None if the model is unavailable or
        content is empty.
    """
    if not content or not content.strip():
        return None
    # Prefix with "content:" so the model understands the semantic role,
    # then delegate to the core embed function.
    return embed_text(f"content: {content.strip()[:1024]}")


def find_similar_article_by_content(
    content_embedding: list[float],
    conn,
    threshold: float = 0.92,
    exclude_id: Optional[int] = None,
) -> Optional[dict]:
    """
    Search for the most similar article based on CONTENT-ONLY embeddings.

    Unlike find_similar_article() which uses the general 'embedding' column
    (title + category + description + content), this function queries the
    'content_embedding' column which stores an embedding of the article's
    body text alone.  This makes deduplication source-agnostic: the same
    news story from TechCrunch and The Verge will be detected as duplicates
    even though their titles, categories, and URLs differ.

    Args:
        content_embedding:  384-dim vector from embed_content_only().
        conn:               Active psycopg2 connection (borrowed from pool).
        threshold:          Cosine similarity threshold (0.0–1.0). Default 0.92.
        exclude_id:         Optional article id to exclude from search
                            (useful to avoid self-matching).

    Returns:
        A dict {id, title, url, similarity} of the nearest match if it
        exceeds the threshold, otherwise None (safe to insert).
    """
    if not _ensure_vector_registered(conn):
        return None

    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL enable_indexscan = off")

            if exclude_id is not None:
                cur.execute(
                    """
                    SELECT id, title, url,
                           1.0 - (content_embedding <=> %s::vector) AS similarity
                    FROM   articles
                    WHERE  content_embedding IS NOT NULL
                      AND  id != %s
                    ORDER  BY content_embedding <=> %s::vector
                    LIMIT  1
                    """,
                    (content_embedding, exclude_id, content_embedding),
                )
            else:
                cur.execute(
                    """
                    SELECT id, title, url,
                           1.0 - (content_embedding <=> %s::vector) AS similarity
                    FROM   articles
                    WHERE  content_embedding IS NOT NULL
                    ORDER  BY content_embedding <=> %s::vector
                    LIMIT  1
                    """,
                    (content_embedding, content_embedding),
                )
            row = cur.fetchone()

        if row is None:
            return None   # no articles with content embeddings yet

        article_id, title, url, similarity = row
        similarity = float(similarity)

        if similarity >= threshold:
            logger.debug(
                "  🔁 Content duplicate detected (sim=%.4f ≥ %.2f) → article_id=%d",
                similarity, threshold, article_id,
            )
            return {
                "id":         article_id,
                "title":      title,
                "url":        url,
                "similarity": similarity,
            }

        return None

    except Exception as exc:
        logger.warning("Content similarity search failed: %s", exc)
        return None


def store_embedding(
    article_id: int,
    embedding: list[float],
    conn,
) -> None:
    """
    Persist the embedding vector for a freshly inserted article.

    Args:
        article_id: The ID returned by the INSERT statement.
        embedding:  384-dim vector from embed_text().
        conn:       Active psycopg2 connection (borrowed from pool).
    """
    if not _ensure_vector_registered(conn):
        return

    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE articles SET embedding = %s::vector WHERE id = %s",
                (embedding, article_id),
            )
    except Exception as exc:
        logger.warning("Failed to store embedding for article %d: %s", article_id, exc)


def store_content_embedding(
    article_id: int,
    content_embedding: list[float],
    conn,
) -> None:
    """
    Persist the content-only embedding for an article.

    Args:
        article_id:        The article's DB id.
        content_embedding: 384-dim vector from embed_content_only().
        conn:              Active psycopg2 connection (borrowed from pool).
    """
    if not _ensure_vector_registered(conn):
        return

    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE articles SET content_embedding = %s::vector WHERE id = %s",
                (content_embedding, article_id),
            )
    except Exception as exc:
        logger.warning("Failed to store content embedding for article %d: %s", article_id, exc)


# ──────────────────────────────────────────────────────────────────────────────
# Source-level semantic deduplication
# ──────────────────────────────────────────────────────────────────────────────

def find_similar_source(
    embedding: list[float],
    conn,
    threshold: float = 0.92,
) -> Optional[dict]:
    """
    Search rss_sources for an existing source that is semantically similar
    to the given embedding.

    Uses pgvector cosine distance (<=>) on the source_embedding column.
    Distance = 1 - cosine_similarity, so:
        similarity >= threshold  ⟺  distance <= (1 - threshold)

    Args:
        embedding:  384-dim vector produced by embed_text() for the candidate source.
        conn:       Active psycopg2 connection (borrowed from pool).
        threshold:  Cosine similarity threshold (0.0–1.0). Default 0.92.

    Returns:
        A dict {id, name, url, category, similarity} for the nearest match if it
        exceeds the threshold, otherwise None (safe to insert).
    """
    if not _ensure_vector_registered(conn):
        return None

    distance_threshold = 1.0 - threshold

    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL enable_indexscan = off")
            cur.execute(
                """
                SELECT id, name, url, category,
                       1.0 - (source_embedding <=> %s::vector) AS similarity
                FROM   rss_sources
                WHERE  source_embedding IS NOT NULL
                ORDER  BY source_embedding <=> %s::vector
                LIMIT  1
                """,
                (embedding, embedding),
            )
            row = cur.fetchone()

        if row is None:
            return None   # no sources with embeddings yet — safe to insert

        source_id, name, url, category, similarity = row
        similarity = float(similarity)

        if similarity >= threshold:
            logger.debug(
                "  🔁 Semantic source duplicate detected (sim=%.4f ≥ %.2f) → source_id=%d",
                similarity, threshold, source_id,
            )
            return {
                "id":         source_id,
                "name":       name,
                "url":        url,
                "category":   category,
                "similarity": similarity,
            }

        return None

    except Exception as exc:
        logger.warning("Source similarity search failed: %s", exc)
        return None


def store_source_embedding(
    source_id: int,
    embedding: list[float],
    conn,
) -> None:
    """
    Persist the embedding vector for a freshly inserted RSS source.

    Args:
        source_id:  The ID returned by the INSERT statement for the new source.
        embedding:  384-dim vector from embed_text().
        conn:       Active psycopg2 connection (borrowed from pool).
    """
    if not _ensure_vector_registered(conn):
        return

    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE rss_sources SET source_embedding = %s::vector WHERE id = %s",
                (embedding, source_id),
            )
    except Exception as exc:
        logger.warning("Failed to store source embedding for source %d: %s", source_id, exc)
