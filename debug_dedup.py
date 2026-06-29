"""
debug_dedup.py
--------------
Debugs the semantic dedup pipeline:
1. Shows recent articles and whether they have content_embedding
2. Computes similarity between the last 5 articles
3. Shows what's stored in is_duplicate / duplicate_of_id columns
"""
import sys
sys.path.insert(0, ".")
import logging
logging.disable(logging.CRITICAL)

from db.connection import init_pool, execute_query, get_connection, release_connection
from db.vector_store import is_vector_ready, find_similar_article_by_content

init_pool()

print()
print("=" * 70)
print("  DEDUP DEBUG REPORT")
print("=" * 70)

# 1. Check last 10 articles and embedding status
rows = execute_query(
    """
    SELECT id, is_duplicate, duplicate_of_id, similarity_score,
           (content_embedding IS NOT NULL) AS has_emb,
           LEFT(url, 60) AS url
    FROM   articles
    ORDER  BY id DESC
    LIMIT  10
    """,
    fetch=True,
)

print("\n[1] LAST 10 ARTICLES IN DB:")
print(f"  {'ID':<7} {'is_dup':<8} {'dup_of':<8} {'sim_score':<12} {'has_emb':<10} URL")
print("  " + "-" * 80)
for r in rows:
    sim = f"{r['similarity_score']:.4f}" if r['similarity_score'] else "NULL"
    dup_of = str(r['duplicate_of_id']) if r['duplicate_of_id'] else "NULL"
    print(f"  {r['id']:<7} {str(r['is_duplicate']):<8} {dup_of:<8} {sim:<12} {str(r['has_emb']):<10} {r['url']}")

# 2. Count stats
total = execute_query("SELECT COUNT(*) AS c FROM articles", fetch=True)[0]["c"]
has_emb = execute_query("SELECT COUNT(*) AS c FROM articles WHERE content_embedding IS NOT NULL", fetch=True)[0]["c"]
is_dup = execute_query("SELECT COUNT(*) AS c FROM articles WHERE is_duplicate = TRUE", fetch=True)[0]["c"]

print(f"\n[2] STATS:")
print(f"  Total articles          : {total}")
print(f"  With content_embedding  : {has_emb}")
print(f"  Marked is_duplicate=TRUE: {is_dup}")

# 3. Test: pick the most recent article with embedding and search for similar
print(f"\n[3] SIMILARITY SEARCH TEST:")
if not is_vector_ready():
    print("  Vector system NOT ready!")
else:
    recent = execute_query(
        "SELECT id, content_embedding, LEFT(url,60) AS url FROM articles WHERE content_embedding IS NOT NULL ORDER BY id DESC LIMIT 1",
        fetch=True,
    )
    if recent:
        r = recent[0]
        print(f"  Using article ID {r['id']} as query vector...")
        conn = get_connection()
        try:
            result = find_similar_article_by_content(
                r["content_embedding"], conn, threshold=0.50  # low threshold to show anything similar
            )
            if result:
                print(f"  Most similar article found:")
                print(f"    ID         : {result['id']}")
                print(f"    URL        : {result['url']}")
                print(f"    Similarity : {result['similarity']:.4f}")
                print(f"  --> At threshold 0.92: {'DUPLICATE' if result['similarity'] >= 0.92 else 'NOT duplicate'}")
            else:
                print("  No similar articles found (only 1 article with embedding?)")
        finally:
            release_connection(conn)

print()
print("=" * 70)
