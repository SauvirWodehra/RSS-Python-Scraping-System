"""
check_articles.py
-----------------
Shows articles currently in the DB with their content_embedding status.
"""
import sys
sys.path.insert(0, ".")
import logging
logging.disable(logging.CRITICAL)

from db.connection import init_pool, execute_query

init_pool()

rows = execute_query(
    """
    SELECT id, title, url,
           (content_embedding IS NOT NULL) AS has_content_emb
    FROM   articles
    ORDER  BY id DESC
    LIMIT  20
    """,
    fetch=True,
)

if not rows:
    print("No articles found in the database.")
else:
    print(f"\n{'ID':<8} {'Content Emb':<14} {'URL'}")
    print("-" * 90)
    for r in rows:
        emb_flag = "YES" if r["has_content_emb"] else "NO"
        url = (r["url"] or "")[:70]
        print(f"{r['id']:<8} {emb_flag:<14} {url}")

    total = execute_query("SELECT COUNT(*) AS cnt FROM articles", fetch=True)[0]["cnt"]
    emb_count = execute_query(
        "SELECT COUNT(*) AS cnt FROM articles WHERE content_embedding IS NOT NULL",
        fetch=True,
    )[0]["cnt"]
    print(f"\nTotal articles in DB : {total}")
    print(f"With content_embedding: {emb_count}")
    print(f"Without              : {total - emb_count}")
