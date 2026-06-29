import sys
sys.path.insert(0, ".")
import logging
logging.disable(logging.CRITICAL)

from db.connection import init_pool, get_connection, release_connection

init_pool()
conn = get_connection()
try:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, default_version, installed_version "
            "FROM pg_available_extensions WHERE name = 'vector'"
        )
        row = cur.fetchone()
        if row:
            print(f"pgvector available: name={row[0]}, default={row[1]}, installed={row[2]}")
        else:
            print("pgvector NOT in pg_available_extensions — server-side package missing.")

        cur.execute("SELECT version()")
        print(cur.fetchone()[0])
finally:
    release_connection(conn)
