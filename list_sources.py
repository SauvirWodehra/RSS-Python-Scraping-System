import sys
sys.path.insert(0, '.')
import logging
logging.disable(logging.CRITICAL)

from db.connection import init_pool, execute_query
init_pool()

sources = execute_query('SELECT name, url, category FROM rss_sources WHERE is_active = TRUE', fetch=True)
print("--- ACTIVE SOURCES ---")
for s in sources:
    print(f"- {s['name']} ({s['category']}) : {s['url']}")
