import sys
sys.path.insert(0, '.')
import logging
logging.disable(logging.CRITICAL)

from db.connection import init_pool, execute_query
init_pool()

rows = execute_query('SELECT id, is_duplicate, duplicate_of_id, similarity_score, LEFT(url, 65) as url FROM articles ORDER BY id DESC LIMIT 5', fetch=True)
print('\n--- LAST 5 ARTICLES IN DB ---')
for r in rows:
    print(f"ID: {r['id']} | is_dup: {r['is_duplicate']} | dup_of: {r['duplicate_of_id']} | sim: {r['similarity_score']} | URL: {r['url']}")

print('\n--- ALL DUPLICATES IN DB ---')
dups = execute_query('SELECT id, is_duplicate, duplicate_of_id, similarity_score, LEFT(url, 65) as url FROM articles WHERE is_duplicate=TRUE ORDER BY id DESC', fetch=True)
if not dups:
    print('No duplicates found yet.')
for d in dups:
    print(f"ID: {d['id']} | is_dup: {d['is_duplicate']} | dup_of: {d['duplicate_of_id']} | sim: {d['similarity_score']} | URL: {d['url']}")
print()
