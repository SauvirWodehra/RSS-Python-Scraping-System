"""
fetch_fresh_urls.py
-------------------
Fetches the latest 2 article URLs from multiple RSS sources
so you have fresh URLs to test with try_add_article.py
"""
import sys
sys.path.insert(0, ".")
import logging
logging.disable(logging.CRITICAL)

import feedparser

sources = [
    ("BBC Sport Football", "https://feeds.bbci.co.uk/sport/football/rss.xml"),
    ("BBC News",           "http://feeds.bbci.co.uk/news/rss.xml"),
    ("TechCrunch",         "https://techcrunch.com/feed/"),
    ("Ars Technica",       "https://feeds.arstechnica.com/arstechnica/index"),
    ("Hacker News",        "https://hnrss.org/frontpage"),
    ("ScienceDaily",       "https://www.sciencedaily.com/rss/all.xml"),
]

print()
print("=" * 80)
print("  FRESH ARTICLE URLs — ready to use with try_add_article.py")
print("=" * 80)

for name, feed_url in sources:
    try:
        f = feedparser.parse(feed_url)
        entries = f.entries[:2]
        if not entries:
            continue
        print(f"\n  [{name}]")
        for e in entries:
            title = (e.get("title") or "")[:65]
            link  = e.get("link") or ""
            print(f"  Title : {title}")
            print(f"  URL   : {link}")
            print()
    except Exception as ex:
        print(f"  [{name}] ERROR: {ex}")

print("=" * 80)
print("  COMMAND TO TEST:")
print('  python try_add_article.py --url "URL_FROM_ABOVE" --source "SOURCE_NAME"')
print("=" * 80)
