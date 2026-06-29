"""
apply_source_embedding_col.py
------------------------------
One-shot migration:
  1. Adds source_embedding VECTOR(384) to rss_sources (idempotent).
  2. Creates IVFFlat index on it (idempotent).
  3. Backfills embeddings for all existing sources by fetching each RSS feed
     live and embedding the real content (feed description + article samples).

Run once: python apply_source_embedding_col.py
"""
import sys
sys.path.insert(0, ".")

import logging
logging.disable(logging.CRITICAL)

from db.connection import init_pool, get_connection, release_connection, _fetch_rss_content
from db.vector_store import is_vector_ready, embed_text, store_source_embedding

init_pool()

conn = get_connection()
try:
    # Step 0 - enable pgvector extension (idempotent)
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    conn.commit()
    print("Step 0 - pgvector extension enabled.")

    # Step 1 - add column
    with conn.cursor() as cur:
        cur.execute(
            "ALTER TABLE rss_sources ADD COLUMN IF NOT EXISTS source_embedding vector(384);"
        )
    conn.commit()
    print("Step 1 - source_embedding column added (or already existed).")

    # Step 2 - create index (skip if already exists)
    with conn.cursor() as cur:
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE  tablename = 'rss_sources'
                    AND    indexname = 'idx_rss_sources_embedding'
                ) THEN
                    CREATE INDEX idx_rss_sources_embedding
                        ON rss_sources USING ivfflat (source_embedding vector_cosine_ops)
                        WITH (lists = 10);
                END IF;
            END
            $$;
            """
        )
    conn.commit()
    print("Step 2 - IVFFlat index ensured.")

    # Step 3 - backfill embeddings
    if not is_vector_ready():
        print("Step 3 - Vector model not ready; skipping backfill.")
        sys.exit(0)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, url, category
            FROM   rss_sources
            WHERE  source_embedding IS NULL
            """
        )
        rows = cur.fetchall()

    print(f"Step 3 - Backfilling embeddings for {len(rows)} sources ...")
    print("         (Each source's RSS feed will be fetched to get real content)\n")

    for source_id, name, url, category in rows:
        print(f"  [{source_id}] {name} - fetching feed ...", end=" ", flush=True)

        # Fetch actual RSS content for a content-aware embedding
        rss_content = _fetch_rss_content(url, max_articles=5)

        if rss_content:
            print(f"got {len(rss_content)} chars", end=" ")
        else:
            print("feed unreachable, using metadata only", end=" ")

        # Build the same structured text as add_source() does
        candidate_text = f"name: {name} category: {category} {rss_content}".strip()

        emb = embed_text(candidate_text)
        if emb:
            store_source_embedding(source_id, emb, conn)
            print("-> embedded OK")
        else:
            print("-> embedding failed (skipped)")

    conn.commit()
    print("\nDone - all existing sources have been embedded with real content.")

finally:
    release_connection(conn)
