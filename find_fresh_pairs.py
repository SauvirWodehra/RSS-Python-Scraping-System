"""
find_fresh_pairs.py
--------------------
1. Fetches latest articles from scrapable RSS sources
2. Checks which ones are NOT already in the DB
3. Groups same-topic articles as potential duplicate pairs
4. Prints ready-to-use try_add_article.py commands
"""
import sys
sys.path.insert(0, ".")
import logging
logging.disable(logging.CRITICAL)

import feedparser
from db.connection import init_pool, execute_query

init_pool()

sources = [
    ("Ars Technica",   "https://feeds.arstechnica.com/arstechnica/index",    "Technology"),
    ("ScienceDaily",   "https://www.sciencedaily.com/rss/all.xml",           "Science"),
    ("BBC News",       "http://feeds.bbci.co.uk/news/rss.xml",               "General"),
    ("BBC Sport",      "https://feeds.bbci.co.uk/sport/football/rss.xml",    "Sports"),
    ("TechCrunch",     "https://techcrunch.com/feed/",                        "Technology"),
    ("New Scientist",  "https://www.newscientist.com/feed/home/",             "Science"),
    ("NPR News",       "https://feeds.npr.org/1001/rss.xml",                  "General"),
]

print()
print("=" * 75)
print("  CHECKING RSS FEEDS vs DB — FINDING FRESH ARTICLES")
print("=" * 75)

fresh = []   # (source, url, title, category)

for name, feed_url, category in sources:
    try:
        f = feedparser.parse(feed_url)
        for entry in f.entries[:8]:
            url   = (entry.get("link") or "").split("?")[0]   # strip tracking params
            title = (entry.get("title") or "").strip()
            if not url or not title:
                continue
            # Check if URL (or base without params) is already in DB
            rows = execute_query(
                "SELECT id FROM articles WHERE url ILIKE %s LIMIT 1",
                (url + "%",),
                fetch=True,
            )
            if not rows:
                fresh.append((name, url, title, category))
    except Exception as ex:
        print(f"  [{name}] Error: {ex}")

print(f"\n  Found {len(fresh)} articles NOT in DB across {len(sources)} sources\n")

# Show top 3 fresh articles per source
from collections import defaultdict
by_source = defaultdict(list)
for item in fresh:
    by_source[item[0]].append(item)

print("  FRESH ARTICLES (not in your DB):")
print("  " + "-" * 70)
all_fresh = []
for name, items in by_source.items():
    for src, url, title, cat in items[:2]:
        print(f"\n  [{src}] ({cat})")
        print(f"  Title : {title[:70]}")
        print(f"  URL   : {url}")
        all_fresh.append((src, url, title, cat))

print()
print("=" * 75)
print("  COMMANDS — run in order:")
print("=" * 75)
for i, (src, url, title, cat) in enumerate(all_fresh[:8], 1):
    print(f'\npython try_add_article.py --url "{url}" --source "{src}" --category "{cat}"')
print()
