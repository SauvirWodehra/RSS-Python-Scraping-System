"""
apply_content_embedding_col.py
------------------------------
One-time migration: backfills the content_embedding column for all articles
that already have full_text but no content_embedding yet.

This is needed so that articles scraped BEFORE content-only dedup was added
can still participate in content-based duplicate detection.

Run from the project root:
    python apply_content_embedding_col.py

The script:
  1. Ensures the schema is up-to-date (adds content_embedding column if missing).
  2. Queries all articles where full_text IS NOT NULL but content_embedding IS NULL.
  3. Generates a content-only embedding for each and stores it.
  4. Reports progress and final counts.
"""

import sys
import logging

sys.path.insert(0, ".")
logging.disable(logging.CRITICAL)

from db.connection import init_pool, init_schema, get_connection, release_connection
from db.vector_store import (
    is_vector_ready,
    embed_content_only,
    store_content_embedding,
)


def main():
    print("=" * 60)
    print("  BACKFILL: content_embedding column")
    print("=" * 60)

    init_pool()
    init_schema()   # ensures content_embedding column exists

    if not is_vector_ready():
        print("\n  ❌ Vector system not ready. Install sentence-transformers + pgvector.")
        sys.exit(1)

    print("  ✅ Vector system ready.\n")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, full_text
                FROM   articles
                WHERE  full_text IS NOT NULL
                  AND  content_embedding IS NULL
                ORDER  BY id
                """
            )
            rows = cur.fetchall()

        total = len(rows)
        print(f"  Found {total} articles to backfill.\n")

        success = 0
        skipped = 0
        for i, (article_id, full_text) in enumerate(rows, 1):
            text = (full_text or "").strip()
            if not text:
                skipped += 1
                continue

            emb = embed_content_only(text)
            if emb is not None:
                store_content_embedding(article_id, emb, conn)
                conn.commit()
                success += 1
            else:
                skipped += 1

            if i % 50 == 0 or i == total:
                print(f"  Progress: {i}/{total}  (embedded: {success}, skipped: {skipped})")

        print()
        print("=" * 60)
        print(f"  DONE — embedded: {success} | skipped: {skipped} | total: {total}")
        print("=" * 60)
        print()

    except Exception as exc:
        conn.rollback()
        print(f"\n  ERROR: {exc}")
    finally:
        release_connection(conn)


if __name__ == "__main__":
    main()
