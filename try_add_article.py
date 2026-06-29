"""
try_add_article.py
------------------
Command-line tool to add an article to the database with content-only
semantic deduplication.

Behaviour:
  - Fetches the article content AUTOMATICALLY from the given URL
    (using Newspaper4k + Playwright/BeautifulSoup fallback).
  - Embeds the fetched CONTENT ONLY (article body text).
  - Checks for semantic similarity against existing articles in the DB.
  - If a semantic duplicate IS FOUND:
      -> prints the duplicate article's ID, title, URL, and similarity score.
      -> STILL inserts the article with is_duplicate=TRUE, duplicate_of_id,
         and similarity_score stored in DB.
  - If NO semantic duplicate is found:
      -> inserts the article into the DB with is_duplicate=FALSE.
      -> stores the content embedding for future dedup checks.
  - If the URL already exists exactly: reports URL-duplicate (no insert).

Usage:
    python try_add_article.py --url URL
                              [--source SOURCE_NAME]
                              [--category CATEGORY]
                              [--threshold 0.92]
                              [--cleanup]

Examples:
    # Add a TechCrunch article:
    python try_add_article.py --url "https://techcrunch.com/some-article" --source "TechCrunch"

    # Add same story from The Verge (stored as duplicate with reference to original):
    python try_add_article.py --url "https://www.theverge.com/same-story" --source "The Verge"

    # Add a completely different article (inserted as new):
    python try_add_article.py --url "https://www.bbc.com/sport/different-topic" --source "BBC Sport"
"""

import sys
import argparse
import logging
import time

# Force UTF-8 output so emoji/special chars do not crash on Windows cp1252
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, ".")

# Only suppress low-level library noise, keep our prints
logging.disable(logging.WARNING)

from db.connection import init_pool, get_connection, release_connection
from db.vector_store import (
    is_vector_ready,
    embed_content_only,
    find_similar_article_by_content,
    store_content_embedding,
    embed_text,
    store_embedding,
)
from config.settings import VECTOR_SIM_THRESHOLD


# -----------------------------------------------------------------------------
# Article fetching -- reuses the existing extraction pipeline
# -----------------------------------------------------------------------------

def _fetch_article_from_url(url: str) -> dict:
    """
    Fetch full article content from a URL using the existing pipeline:
      1. Newspaper4k (primary extractor)
      2. Playwright + BeautifulSoup (fallback for JS-rendered / blocked sites)

    Returns:
        dict with keys: title, full_text, author, published_at, summary
    """
    from pipeline.article_extractor import extract_article

    # Build a minimal raw article dict (as if it came from RSS collector)
    raw = {
        "url": url,
        "title": "",
        "summary": "",
        "author": "",
        "published": None,
    }

    enriched = extract_article(raw)

    return {
        "title":        enriched.get("title") or "",
        "full_text":    enriched.get("full_text") or "",
        "author":       enriched.get("author") or "",
        "published_at": enriched.get("published_at"),
        "summary":      enriched.get("summary") or "",
    }


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _try_insert(
    conn, url, title, summary, full_text,
    is_duplicate=False, duplicate_of_id=None, similarity_score=None,
) -> tuple:
    """
    Insert the article into the DB.

    Returns:
        ("inserted", new_id)     -- brand-new URL, inserted successfully
        ("url_duplicate", None)  -- exact URL already in DB (ON CONFLICT)
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO articles
                    (source_id, title, url, summary, full_text, is_clean,
                     is_duplicate, duplicate_of_id, similarity_score)
                VALUES
                    (NULL, %s, %s, %s, %s, FALSE,
                     %s, %s, %s)
                ON CONFLICT (url) DO NOTHING
                RETURNING id
                """,
                (
                    title or "(untitled)", url, summary or None, full_text or None,
                    is_duplicate, duplicate_of_id, similarity_score,
                ),
            )
            row = cur.fetchone()
        conn.commit()
        if row:
            return "inserted", row[0]
        else:
            return "url_duplicate", None
    except Exception:
        conn.rollback()
        raise


def _build_general_embed_text(title, category, summary, content):
    """Build structured labeled text for the general embedding column."""
    parts = []
    if title:    parts.append(f"title: {title.strip()}")
    if category: parts.append(f"category: {category.strip()}")
    if summary:  parts.append(f"description: {summary.strip()[:400]}")
    if content:  parts.append(f"content: {content.strip()[:500]}")
    return " ".join(parts)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Add an article (by URL) with content-only semantic deduplication.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--url",       required=True, help="Article URL to fetch and add")
    parser.add_argument("--source",    default="(manual)", help="Source name (for display only)")
    parser.add_argument("--category",  default="General", help="Category (e.g. Tech, Sports)")
    parser.add_argument("--threshold", type=float, default=None,
                        help=f"Similarity threshold override (default: {VECTOR_SIM_THRESHOLD})")
    parser.add_argument("--cleanup",   action="store_true",
                        help="Delete the inserted row after the test (for safe testing)")
    args = parser.parse_args()

    threshold = args.threshold if args.threshold is not None else VECTOR_SIM_THRESHOLD

    print()
    print("=" * 70)
    print("  [ARTICLE INSERTION] CONTENT-ONLY SEMANTIC DEDUP")
    print(f"  Similarity threshold : {threshold}")
    print(f"  Dedup compares       : CONTENT ONLY (ignores title/url/source)")
    print("=" * 70)
    print(f"  Source : {args.source}")
    print(f"  URL    : {args.url}")
    print("=" * 70)

    # -- Step 1: Fetch article content from URL --------------------------------
    print()
    print("  [Step 1] Fetching article content from URL ...")
    start_time = time.time()

    try:
        fetched = _fetch_article_from_url(args.url)
    except Exception as exc:
        print(f"\n  [ERROR] Could not fetch article from URL: {exc}")
        print("     Make sure the URL is accessible and try again.")
        print()
        return

    elapsed = time.time() - start_time
    title     = fetched["title"]
    full_text = fetched["full_text"]
    summary   = fetched["summary"]
    author    = fetched["author"]

    print(f"  Fetched in {elapsed:.1f}s")
    print(f"  Title   : {title[:100] if title else '(could not extract)'}")
    print(f"  Author  : {author[:80] if author else '(unknown)'}")
    print(f"  Content : {len(full_text)} chars extracted")

    if not full_text or len(full_text) < 50:
        print()
        print("  [WARNING] Very little content extracted from URL.")
        print("     Semantic dedup may not work well with short text.")
        print("     The article will still be inserted with URL-only dedup.")

    # -- Step 2: Init DB & vector system ---------------------------------------
    init_pool()
    vector_active = is_vector_ready()

    if not vector_active:
        print()
        print("  [WARNING] Vector dedup is NOT ready.")
        print('  Run: python -c "from db.connection import init_pool,init_schema; init_pool(); init_schema()"')
        print("  to apply the pgvector schema, then retry.")

    conn = get_connection()
    new_id = None

    try:
        # -- Step 3: Embed CONTENT & check semantic similarity -----------------
        content_dup = None
        content_embedding = None

        if vector_active and full_text and len(full_text) >= 50:
            print()
            print("  [Step 2] Embedding article CONTENT for semantic search ...")
            content_embedding = embed_content_only(full_text)

            if content_embedding is not None:
                print("  [Step 3] Searching for semantically similar articles ...")
                content_dup = find_similar_article_by_content(
                    content_embedding, conn, threshold=threshold
                )
            else:
                print("  [WARNING] Could not generate content embedding.")

        # -- Handle semantic duplicate: INSERT with duplicate flags ------------
        is_dup        = content_dup is not None
        dup_of_id     = content_dup["id"]        if is_dup else None
        dup_sim_score = content_dup["similarity"] if is_dup else None

        if is_dup:
            print()
            print("=" * 70)
            print("  [DUPLICATE FOUND] STORING WITH DUPLICATE FLAG")
            print("=" * 70)
            print(f"  Duplicate of article ID : {dup_of_id}")
            print(f"  Original title          : {content_dup['title']}")
            print(f"  Original URL            : {content_dup['url']}")
            print(f"  Similarity score        : {dup_sim_score:.4f}")
            print(f"  Threshold               : {threshold}")
            print()
            print("  The article WILL still be stored in DB with:")
            print(f"    is_duplicate     = TRUE")
            print(f"    duplicate_of_id  = {dup_of_id}")
            print(f"    similarity_score = {dup_sim_score:.4f}")
            print("=" * 70)

        # -- Step 4: INSERT the article (original OR duplicate) ---------------
        print()
        if is_dup:
            print("  [Step 4] Inserting article with duplicate tracking metadata ...")
        else:
            print("  [Step 4] No semantic duplicate found -- inserting article ...")

        status, new_id = _try_insert(
            conn, args.url, title, summary, full_text,
            is_duplicate=is_dup,
            duplicate_of_id=dup_of_id,
            similarity_score=dup_sim_score,
        )

        if status == "url_duplicate":
            print()
            print("=" * 70)
            print("  [RESULT] URL DUPLICATE")
            print("  The exact URL already exists in the database.")
            print("  No row was inserted.")
            print("=" * 70)
            print()
            return

        # -- Step 5: Store embeddings ------------------------------------------
        if new_id and content_embedding is not None:
            print("  [Step 5] Storing content embedding for future dedup ...")
            try:
                store_content_embedding(new_id, content_embedding, conn)
            except Exception:
                pass

        # Also store the general embedding for backward compat
        if new_id and vector_active:
            general_text = _build_general_embed_text(
                title, args.category, summary, full_text
            )
            general_emb = embed_text(general_text)
            if general_emb is not None:
                try:
                    store_embedding(new_id, general_emb, conn)
                except Exception:
                    pass

        conn.commit()

        # -- Report success ----------------------------------------------------
        print()
        print("=" * 70)
        if is_dup:
            print("  [RESULT] DUPLICATE ARTICLE STORED")
        else:
            print("  [RESULT] NEW ARTICLE INSERTED")
        print("=" * 70)
        print(f"  New article ID   : {new_id}")
        print(f"  Source           : {args.source}")
        print(f"  Title            : {title[:100] if title else '(untitled)'}")
        print(f"  Content length   : {len(full_text)} chars")
        if is_dup:
            print(f"  is_duplicate     : TRUE")
            print(f"  duplicate_of_id  : {dup_of_id}")
            print(f"  similarity_score : {dup_sim_score:.4f}")
        else:
            print(f"  is_duplicate     : FALSE")
        print("=" * 70)

        # -- Optional cleanup --------------------------------------------------
        if args.cleanup and new_id is not None:
            print()
            print(f"  [Cleanup] Removing test row id={new_id} ...")
            with conn.cursor() as cur:
                cur.execute("DELETE FROM articles WHERE id = %s", (new_id,))
            conn.commit()
            print("  Done.")

    except Exception as exc:
        print(f"\n  ERROR: {exc}")
    finally:
        release_connection(conn)
        # Clean up Playwright browser if it was started
        try:
            from pipeline.web_scraper import close_browser
            close_browser()
        except Exception:
            pass

    print()


if __name__ == "__main__":
    main()
