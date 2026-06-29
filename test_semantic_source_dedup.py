"""
test_semantic_source_dedup.py
------------------------------
Quick smoke test for the semantic source deduplication feature.
Tests two cases:
  1. Adding a genuinely new source  → should succeed
  2. Adding a near-duplicate source → should be rejected
"""
import sys
sys.path.insert(0, ".")
import logging
logging.disable(logging.CRITICAL)

from db.connection import init_pool, add_source, remove_source, SemanticDuplicateError

init_pool()

# ── Test 1: Unique source (should succeed) ────────────────────────────────────
print("\n[Test 1] Adding a genuinely NEW source (Indian cooking blog)...")
try:
    new_id = add_source("Archana's Kitchen", "https://www.archanaskitchen.com/rss", "Food")
    print(f"  PASS — Added with ID {new_id}")
    # Clean up
    remove_source(new_id)
    print(f"  Cleaned up source {new_id}")
except SemanticDuplicateError as e:
    print(f"  FAIL — Was incorrectly rejected as duplicate: {e}")
except Exception as e:
    print(f"  ERROR — {e}")

# ── Test 2: Semantic duplicate of TechCrunch ─────────────────────────────────
print("\n[Test 2] Adding a SEMANTIC DUPLICATE of TechCrunch...")
try:
    new_id = add_source("TechCrunch News Feed", "https://techcrunch.com/rss/", "Technology")
    print(f"  FAIL — Should have been rejected but got ID {new_id}")
    remove_source(new_id)
except SemanticDuplicateError as e:
    m = e.match
    print(f"  PASS — Correctly rejected!")
    print(f"         Matched existing source: [{m['id']}] {m['name']} (similarity={m['similarity']:.4f})")
except Exception as e:
    print(f"  ERROR — {e}")

print()
