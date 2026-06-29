"""
test_dedup_e2e.py
-----------------
End-to-end test for semantic duplicate detection.

WHAT IT DOES:
  1. Inserts a "seed" article with known content directly into DB
     and stores its content_embedding.
  2. Attempts to add a SECOND article (same story, slightly reworded).
  3. Shows whether is_duplicate=TRUE is stored in DB.
  4. Cleans up test rows after the test.

This proves the dedup logic works correctly independent of URL scraping.
"""
import sys
sys.path.insert(0, ".")
import logging
logging.disable(logging.CRITICAL)

from db.connection import init_pool, get_connection, release_connection, execute_query
from db.vector_store import (
    is_vector_ready, embed_content_only,
    find_similar_article_by_content, store_content_embedding,
)
from config.settings import VECTOR_SIM_THRESHOLD

# ── Test content ───────────────────────────────────────────────────────────────
# Article A: original
ARTICLE_A_URL     = "https://test-dedup-seed.example.com/article-a"
ARTICLE_A_CONTENT = """
Robert Lewandowski is set to leave Barcelona and join Chicago Fire in Major League Soccer.
The Polish striker, 37, has agreed to a deal with the MLS club after his contract at the
Catalan giants expires. Chicago Fire head coach Gregg Berhalter confirmed the club's interest
in signing the veteran forward. Lewandowski scored 27 goals for Barcelona last season.
The transfer is seen as a major coup for MLS as they continue to attract world-class talent.
Pini Zahavi, Lewandowski's agent, visited Chicago earlier this month to finalise terms.
The player is expected to sign a two-year deal worth around $15 million per season.
"""

# Article B: same story, slightly reworded (as a different news source would write it)
ARTICLE_B_URL     = "https://test-dedup-variant.example.com/article-b"
ARTICLE_B_CONTENT = """
Barcelona's star striker Robert Lewandowski is heading to MLS side Chicago Fire after
his contract with the Spanish club runs out. The 37-year-old Polish forward has reached
an agreement with Chicago Fire after agent Pini Zahavi held talks with the club's management.
Coach Gregg Berhalter said Chicago Fire have been targeting Lewandowski for months.
The move, worth approximately $15m a year over two seasons, represents a landmark signing
for Major League Soccer. Lewandowski netted 27 times for Barca in the previous campaign.
"""

# ── Setup ──────────────────────────────────────────────────────────────────────
init_pool()
inserted_ids = []

print()
print("=" * 70)
print("  END-TO-END SEMANTIC DEDUP TEST")
print(f"  Threshold: {VECTOR_SIM_THRESHOLD}")
print("=" * 70)

if not is_vector_ready():
    print("  [ERROR] Vector system not ready. Exiting.")
    sys.exit(1)

conn = get_connection()
try:
    # ── STEP 1: Insert Article A (original) ───────────────────────────────────
    print()
    print("[Step 1] Inserting ORIGINAL article (Article A)...")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO articles (title, url, full_text, is_clean, is_duplicate)
            VALUES (%s, %s, %s, FALSE, FALSE)
            ON CONFLICT (url) DO NOTHING
            RETURNING id
            """,
            ("Test: Lewandowski joins Chicago Fire", ARTICLE_A_URL, ARTICLE_A_CONTENT.strip()),
        )
        row = cur.fetchone()
    conn.commit()

    if row:
        id_a = row[0]
        inserted_ids.append(id_a)
        print(f"  Article A inserted with ID: {id_a}")
    else:
        # Already exists — get its ID
        id_a = execute_query(f"SELECT id FROM articles WHERE url = '{ARTICLE_A_URL}'", fetch=True)[0]["id"]
        print(f"  Article A already exists with ID: {id_a}")

    # Store content embedding for Article A
    emb_a = embed_content_only(ARTICLE_A_CONTENT.strip())
    if emb_a:
        store_content_embedding(id_a, emb_a, conn)
        conn.commit()
        print(f"  Content embedding stored for Article A (ID {id_a})")
    else:
        print("  [ERROR] Could not embed Article A content")
        sys.exit(1)

    # ── STEP 2: Check similarity before inserting Article B ───────────────────
    print()
    print("[Step 2] Embedding Article B content and searching for duplicates...")
    emb_b = embed_content_only(ARTICLE_B_CONTENT.strip())
    if not emb_b:
        print("  [ERROR] Could not embed Article B content")
        sys.exit(1)

    dup_result = find_similar_article_by_content(emb_b, conn, threshold=VECTOR_SIM_THRESHOLD)

    if dup_result:
        print(f"  [DUPLICATE FOUND]")
        print(f"    Duplicate of article ID : {dup_result['id']}")
        print(f"    URL                     : {dup_result['url']}")
        print(f"    Similarity score        : {dup_result['similarity']:.4f}")
        print(f"    Threshold               : {VECTOR_SIM_THRESHOLD}")
        is_dup    = True
        dup_of_id = dup_result["id"]
        dup_score = dup_result["similarity"]
    else:
        # Show actual similarity score even if below threshold
        with conn.cursor() as cur:
            cur.execute("SET LOCAL enable_indexscan = off")
            cur.execute(
                """
                SELECT id, 1.0 - (content_embedding <=> %s::vector) AS sim
                FROM   articles
                WHERE  content_embedding IS NOT NULL AND id = %s
                LIMIT 1
                """,
                (emb_b, id_a),
            )
            row = cur.fetchone()
        actual_sim = row[1] if row else 0
        print(f"  [NO DUPLICATE] Similarity with Article A: {actual_sim:.4f}")
        print(f"  Threshold is {VECTOR_SIM_THRESHOLD} — content may need to be more similar")
        is_dup    = False
        dup_of_id = None
        dup_score = None

    # ── STEP 3: Insert Article B with duplicate metadata ──────────────────────
    print()
    print("[Step 3] Inserting Article B with duplicate metadata...")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO articles
                (title, url, full_text, is_clean,
                 is_duplicate, duplicate_of_id, similarity_score)
            VALUES (%s, %s, %s, FALSE, %s, %s, %s)
            ON CONFLICT (url) DO NOTHING
            RETURNING id
            """,
            (
                "Test variant: Lewandowski to Chicago Fire (Reuters)",
                ARTICLE_B_URL,
                ARTICLE_B_CONTENT.strip(),
                is_dup, dup_of_id, dup_score,
            ),
        )
        row = cur.fetchone()
    conn.commit()

    if row:
        id_b = row[0]
        inserted_ids.append(id_b)
    else:
        id_b = execute_query(f"SELECT id FROM articles WHERE url = '{ARTICLE_B_URL}'", fetch=True)[0]["id"]

    # Store content embedding for B too
    store_content_embedding(id_b, emb_b, conn)
    conn.commit()

    print(f"  Article B inserted with ID: {id_b}")

    # ── STEP 4: Verify DB ─────────────────────────────────────────────────────
    print()
    print("[Step 4] Verifying DB records...")
    rows = execute_query(
        f"SELECT id, is_duplicate, duplicate_of_id, similarity_score, LEFT(url,50) AS url "
        f"FROM articles WHERE id IN ({id_a},{id_b}) ORDER BY id",
        fetch=True,
    )
    print(f"  {'ID':<8} {'is_duplicate':<14} {'duplicate_of_id':<18} {'similarity_score':<18} URL")
    print("  " + "-" * 80)
    for r in rows:
        sim = f"{r['similarity_score']:.4f}" if r['similarity_score'] else "NULL"
        dup_of = str(r['duplicate_of_id']) if r['duplicate_of_id'] else "NULL"
        print(f"  {r['id']:<8} {str(r['is_duplicate']):<14} {dup_of:<18} {sim:<18} {r['url']}")

    print()
    print("=" * 70)
    if is_dup:
        print("  [RESULT] SUCCESS -- Duplicate correctly detected and stored!")
        print(f"           Article B (ID {id_b}) -> is_duplicate=TRUE, duplicate_of_id={dup_of_id}")
    else:
        print("  [RESULT] Duplicate NOT detected (similarity below threshold)")
        print("           Check the actual score above and lower VECTOR_SIM_THRESHOLD if needed")
    print("=" * 70)

finally:
    release_connection(conn)

# ── CLEANUP ───────────────────────────────────────────────────────────────────
print()
ans = input("Delete test rows from DB? (yes/no): ").strip().lower()
if ans == "yes":
    for test_id in inserted_ids:
        execute_query(f"DELETE FROM articles WHERE id = {test_id}")
    print(f"  Deleted test rows: {inserted_ids}")
else:
    print(f"  Test rows kept in DB: {inserted_ids}")
    print(f"  Check in pgAdmin: SELECT id, is_duplicate, duplicate_of_id, similarity_score FROM articles WHERE id IN ({','.join(map(str,inserted_ids))})")
print()
