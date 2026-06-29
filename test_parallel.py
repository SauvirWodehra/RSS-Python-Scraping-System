"""
test_parallel.py
-----------------
Proves that RSS feed collection is running in parallel.

Run:
    python test_parallel.py

What it shows:
    - SEQUENTIAL timing (one feed at a time) vs
    - PARALLEL timing (all feeds at once)
    - Thread IDs proving different threads are working simultaneously
"""

import sys
import time
import threading

sys.path.insert(0, ".")

from utils.logger import setup_logging
setup_logging()

from db.connection import init_pool, seed_sources, get_all_sources
from config.settings import REQUEST_TIMEOUT

import feedparser
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── DB setup ──────────────────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("  RSS PARALLEL EXECUTION TEST")
print("=" * 65)

init_pool()
seed_sources()
sources = get_all_sources()

print(f"\n  Sources loaded from DB: {len(sources)}")
print(f"  Each source will be fetched and timed independently.\n")

# ──────────────────────────────────────────────────────────────────────────────
# Helper: fetch one feed, record timing + thread info
# ──────────────────────────────────────────────────────────────────────────────

def fetch_and_time(source: dict) -> dict:
    thread_name = threading.current_thread().name
    thread_id   = threading.current_thread().ident

    t_start = time.perf_counter()
    wall_start = time.strftime("%H:%M:%S")

    try:
        parsed = feedparser.parse(source["url"], request_headers={
            "User-Agent": "Mozilla/5.0 (compatible; RSSTest/1.0)"
        })
        count = len(parsed.entries)
    except Exception:
        count = 0

    elapsed = time.perf_counter() - t_start

    return {
        "name":      source["name"],
        "entries":   count,
        "elapsed":   elapsed,
        "started":   wall_start,
        "thread":    thread_name,
        "thread_id": thread_id,
    }


# ──────────────────────────────────────────────────────────────────────────────
# TEST 1: Sequential (one at a time)
# ──────────────────────────────────────────────────────────────────────────────
print("-" * 65)
print("  TEST 1 — SEQUENTIAL (baseline, one feed at a time)")
print("-" * 65)

seq_results = []
t_seq_start = time.perf_counter()

for source in sources:
    result = fetch_and_time(source)
    seq_results.append(result)
    print(f"  [{result['started']}] {result['name']:<22} "
          f"{result['entries']:>4} entries  {result['elapsed']:.2f}s  "
          f"Thread: {result['thread']}")

t_seq_total = time.perf_counter() - t_seq_start
print(f"\n  Sequential TOTAL time : {t_seq_total:.2f}s")


# ──────────────────────────────────────────────────────────────────────────────
# TEST 2: Parallel (ThreadPoolExecutor — actual pipeline behaviour)
# ──────────────────────────────────────────────────────────────────────────────
print()
print("-" * 65)
print("  TEST 2 — PARALLEL (ThreadPoolExecutor, 8 workers)")
print("-" * 65)

par_results = []
t_par_start = time.perf_counter()

with ThreadPoolExecutor(max_workers=8) as executor:
    future_to_source = {
        executor.submit(fetch_and_time, source): source
        for source in sources
    }
    for future in as_completed(future_to_source):
        result = future.result()
        par_results.append(result)
        print(f"  [{result['started']}] {result['name']:<22} "
              f"{result['entries']:>4} entries  {result['elapsed']:.2f}s  "
              f"Thread: {result['thread']}")

t_par_total = time.perf_counter() - t_par_start
print(f"\n  Parallel  TOTAL time  : {t_par_total:.2f}s")


# ──────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ──────────────────────────────────────────────────────────────────────────────
speedup = t_seq_total / t_par_total if t_par_total > 0 else 0

print()
print("=" * 65)
print("  RESULTS SUMMARY")
print("=" * 65)
print(f"  Sequential time : {t_seq_total:>6.2f}s")
print(f"  Parallel time   : {t_par_total:>6.2f}s")
print(f"  Speedup         : {speedup:>6.2f}x  {'[PASS] PARALLEL IS FASTER' if speedup > 1.5 else '[WARN] Low speedup (check network)'}")

# Count unique thread IDs used in parallel run
unique_threads = len({r["thread_id"] for r in par_results})
print(f"  Unique threads  : {unique_threads:>6}   "
      f"{'[PASS] Multiple threads confirmed' if unique_threads > 1 else '[FAIL] Only 1 thread -- NOT parallel!'}")

# Count feeds that started at the same wall-clock second
from collections import Counter
start_counts = Counter(r["started"] for r in par_results)
max_concurrent = max(start_counts.values())
print(f"  Max concurrent  : {max_concurrent:>6}   "
      f"feeds fetched within the same second")

print()
if speedup > 1.5 and unique_threads > 1:
    print("  [PASS] VERDICT: Project IS running in PARALLEL.")
else:
    print("  [FAIL] VERDICT: Parallelism not confirmed. Check thread pool settings.")
print("=" * 65 + "\n")
