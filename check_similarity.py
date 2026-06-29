"""
check_similarity.py
--------------------
Checks the actual cosine similarity between two specific articles
to understand why duplicate detection isn't triggering.

Usage:
    python check_similarity.py --id1 2973 --id2 2976
"""
import sys
sys.path.insert(0, ".")
import logging
logging.disable(logging.CRITICAL)
import argparse

from db.connection import init_pool, execute_query, get_connection, release_connection
from db.vector_store import is_vector_ready

def check(id1, id2):
    init_pool()
    if not is_vector_ready():
        print("Vector system NOT ready.")
        return

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL enable_indexscan = off")
            cur.execute(
                """
                SELECT
                    a.id AS id1,
                    b.id AS id2,
                    LEFT(a.url, 55) AS url1,
                    LEFT(b.url, 55) AS url2,
                    ROUND((1.0 - (a.content_embedding <=> b.content_embedding))::numeric, 4) AS similarity,
                    (a.content_embedding IS NOT NULL) AS a_has_emb,
                    (b.content_embedding IS NOT NULL) AS b_has_emb
                FROM articles a, articles b
                WHERE a.id = %s AND b.id = %s
                """,
                (id1, id2),
            )
            row = cur.fetchone()

        if not row:
            print(f"Could not find articles {id1} and/or {id2}")
            return

        id1, id2, url1, url2, sim, a_emb, b_emb = row
        print()
        print("=" * 65)
        print("  SIMILARITY CHECK")
        print("=" * 65)
        print(f"  Article {id1}: {url1}")
        print(f"    has content_embedding : {a_emb}")
        print()
        print(f"  Article {id2}: {url2}")
        print(f"    has content_embedding : {b_emb}")
        print()
        print(f"  Cosine similarity      : {sim}")
        print()
        if sim is None:
            print("  [RESULT] Cannot compare -- one or both embeddings are NULL")
        elif sim >= 0.92:
            print(f"  [RESULT] WOULD BE DETECTED as DUPLICATE at threshold 0.92")
        elif sim >= 0.80:
            print(f"  [RESULT] Similar but BELOW threshold 0.92")
            print(f"           Would be caught at threshold {float(sim) - 0.01:.2f} or lower")
        else:
            print(f"  [RESULT] Content is NOT similar enough (score too low)")
        print("=" * 65)
        print()

    finally:
        release_connection(conn)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--id1", type=int, required=True, help="First article ID")
    parser.add_argument("--id2", type=int, required=True, help="Second article ID")
    args = parser.parse_args()
    check(args.id1, args.id2)
