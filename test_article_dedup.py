"""
test_article_dedup.py
---------------------
Smoke test for CONTENT-ONLY article semantic deduplication.

Tests whether the pipeline catches a newly submitted article whose
CONTENT (body text) is semantically similar to one already stored,
even if the title, URL, source, and category are completely different.

Checks performed:
  1. Embed + store a "seed" article directly via vector_store helpers.
  2. Try an article with SIMILAR content (same story, different source)
     → must be flagged as duplicate.
  3. Try an article with DIFFERENT content (unrelated topic)
     → must be allowed through.
  4. Clean up the seed row from the DB.

Run from the project root:
    python test_article_dedup.py
"""

import sys
import logging
sys.path.insert(0, ".")

# ── Keep output clean — only show our own prints ─────────────────────────────
logging.disable(logging.CRITICAL)

from db.connection import init_pool, get_connection, release_connection
from db.vector_store import (
    is_vector_ready,
    embed_content_only,
    find_similar_article_by_content,
    store_content_embedding,
)
from config.settings import VECTOR_SIM_THRESHOLD

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _insert_seed_article(conn, article: dict) -> int | None:
    """
    Directly insert a minimal test row into articles and store its
    content-only embedding. Returns the new article id, or None on failure.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO articles (source_id, title, url, summary, full_text, is_clean)
                VALUES (NULL, %(title)s, %(url)s, %(summary)s, %(full_text)s, FALSE)
                RETURNING id
                """,
                article,
            )
            row = cur.fetchone()
        if row is None:
            return None
        new_id = row[0]

        # Store CONTENT-ONLY embedding (used for the new dedup)
        content_text = (article.get("full_text") or "").strip()
        if content_text:
            content_emb = embed_content_only(content_text)
            if content_emb:
                store_content_embedding(new_id, content_emb, conn)

        conn.commit()
        return new_id
    except Exception as exc:
        conn.rollback()
        print(f"  [seed insert error] {exc}")
        return None


def _delete_article(conn, article_id: int) -> None:
    """Remove the test article row."""
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM articles WHERE id = %s", (article_id,))
        conn.commit()
    except Exception:
        conn.rollback()


# ─────────────────────────────────────────────────────────────────────────────
# Test articles
# ─────────────────────────────────────────────────────────────────────────────

# SEED: Original article — from TechCrunch
SEED_ARTICLE = {
    "title":     "Apple announces new M4 MacBook Pro with AI features",
    "url":       "https://test-seed.example.com/apple-m4-macbook",
    "category":  "Technology",
    "summary":   (
        "Apple has unveiled the new MacBook Pro featuring the M4 chip "
        "with dedicated Neural Engine for on-device AI processing."
    ),
    "full_text": (
        "Cupertino, California — Apple today announced the MacBook Pro M4. "
        "The new model includes Apple Intelligence, a suite of AI tools built "
        "directly into macOS. The M4 chip delivers significant CPU and GPU gains "
        "over the previous M3 generation and introduces hardware-accelerated "
        "machine learning. Pricing starts at $1,999 and ships next month."
    ),
}

# SIMILAR: Same story, different source (The Verge), reworded content
# Should be BLOCKED — the content talks about the same event
SIMILAR_ARTICLE = {
    "title":     "MacBook Pro M4 with on-device AI launched by Apple",
    "url":       "https://test-similar.example.com/verge-macbook-m4",
    "category":  "Gadgets",   # Different category!
    "summary":   "Apple revealed a refreshed MacBook Pro powered by M4.",
    "full_text": (
        "Apple held a press event to introduce the MacBook Pro with M4 chip. "
        "The company highlighted Apple Intelligence capabilities and improved "
        "battery life. The M4 brings hardware ML acceleration and faster cores. "
        "The device goes on sale next month starting at around two thousand dollars."
    ),
}

# DIFFERENT: Completely unrelated topic
# Should PASS — content is about cricket, not laptops
DIFFERENT_ARTICLE = {
    "title":     "India wins the ICC Cricket World Cup 2024",
    "url":       "https://test-different.example.com/india-cricket-worldcup",
    "category":  "Sports",
    "summary":   "India defeated South Africa in the T20 World Cup final.",
    "full_text": (
        "Bridgetown, Barbados — The Indian cricket team, led by Rohit Sharma, "
        "clinched the ICC Men's T20 World Cup 2024 after defeating South Africa "
        "by 7 runs in a nail-biting final. Virat Kohli was named Player of the Match."
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Run tests
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  ARTICLE CONTENT-ONLY SEMANTIC DEDUP — SMOKE TEST")
    print(f"  Similarity threshold : {VECTOR_SIM_THRESHOLD}")
    print(f"  Dedup compares       : CONTENT ONLY (ignores title/url/category)")
    print("=" * 70)

    init_pool()

    if not is_vector_ready():
        print("\n  Vector deduplication is NOT ready.")
        print("  Make sure sentence-transformers and pgvector are installed,")
        print("  and that the pgvector extension is active in PostgreSQL.")
        sys.exit(1)

    print("\n  ✅ Vector deduplication is ACTIVE.\n")

    conn = get_connection()
    seed_id = None

    try:
        # ── Seed: insert the reference article ───────────────────────────────
        print("[Setup] Inserting seed article into DB ...")
        print(f"        Source  : TechCrunch (simulated)")
        print(f"        Title   : {SEED_ARTICLE['title']}")
        print(f"        Content : {SEED_ARTICLE['full_text'][:80]}...")
        seed_id = _insert_seed_article(conn, SEED_ARTICLE)
        if seed_id is None:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM articles WHERE url = %s", (SEED_ARTICLE["url"],))
                row = cur.fetchone()
            if row:
                seed_id = row[0]
                print(f"  Seed article already present (id={seed_id}), reusing it.")
            else:
                print("  Could not insert or find seed article. Aborting.")
                return
        else:
            print(f"  ✅ Seed article inserted with id={seed_id}")

        print()

        # ── Test 1: Same story from a DIFFERENT source ───────────────────────
        print("-" * 70)
        print("[Test 1] Same story, different source (content is semantically similar)")
        print(f"         Source   : The Verge (simulated)")
        print(f"         Title    : {SIMILAR_ARTICLE['title']}")
        print(f"         Category : {SIMILAR_ARTICLE['category']}")
        print(f"         URL      : {SIMILAR_ARTICLE['url']}")
        print(f"         Content  : {SIMILAR_ARTICLE['full_text'][:80]}...")

        sim_embedding = embed_content_only(SIMILAR_ARTICLE["full_text"])
        dup_result = find_similar_article_by_content(
            sim_embedding, conn, threshold=VECTOR_SIM_THRESHOLD
        )

        if dup_result is not None:
            print(f"  ✅ PASS — Correctly BLOCKED as duplicate of article #{dup_result['id']}")
            print(f"           Similarity: {dup_result['similarity']:.4f}")
        else:
            print("  ❌ FAIL — Similar content was NOT caught (similarity below threshold)")
            print("         Try lowering VECTOR_SIM_THRESHOLD in your .env")

        print()

        # ── Test 2: Completely DIFFERENT topic ────────────────────────────────
        print("-" * 70)
        print("[Test 2] Different topic from a different source")
        print(f"         Source   : ESPN (simulated)")
        print(f"         Title    : {DIFFERENT_ARTICLE['title']}")
        print(f"         Category : {DIFFERENT_ARTICLE['category']}")
        print(f"         URL      : {DIFFERENT_ARTICLE['url']}")
        print(f"         Content  : {DIFFERENT_ARTICLE['full_text'][:80]}...")

        diff_embedding = embed_content_only(DIFFERENT_ARTICLE["full_text"])
        not_dup = find_similar_article_by_content(
            diff_embedding, conn, threshold=VECTOR_SIM_THRESHOLD
        )

        if not_dup is None:
            print("  ✅ PASS — Correctly ALLOWED (not a duplicate)")
        else:
            print(f"  ❌ FAIL — Different content was incorrectly flagged as duplicate of #{not_dup['id']}")
            print("         Try raising VECTOR_SIM_THRESHOLD in your .env")

    finally:
        # ── Cleanup ───────────────────────────────────────────────────────────
        if seed_id is not None:
            print()
            print(f"[Cleanup] Removing seed article id={seed_id} ...")
            _delete_article(conn, seed_id)
            print("  Done.")
        release_connection(conn)

    print()
    print("=" * 70)
    print("  TEST COMPLETE")
    print("=" * 70)
    print()


if __name__ == "__main__":
    main()
