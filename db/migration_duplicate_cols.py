"""
db/migration_duplicate_cols.py
--------------------------------
One-shot migration: adds duplicate-tracking columns to the existing `articles`
table for databases created before this feature was added.

Safe to run multiple times (uses ADD COLUMN IF NOT EXISTS).

Run:
    python db/migration_duplicate_cols.py
"""

import sys
sys.path.insert(0, ".")

import logging
logging.disable(logging.CRITICAL)

from db.connection import init_pool, get_connection, release_connection

init_pool()
conn = get_connection()

try:
    with conn.cursor() as cur:
        # 1. Add is_duplicate column
        cur.execute(
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS is_duplicate "
            "BOOLEAN NOT NULL DEFAULT FALSE;"
        )
        print("[OK] is_duplicate column added (or already existed).")

        # 2. Add duplicate_of_id foreign key column
        cur.execute(
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS duplicate_of_id "
            "INT REFERENCES articles(id) ON DELETE SET NULL;"
        )
        print("[OK] duplicate_of_id column added (or already existed).")

        # 3. Add similarity_score column
        cur.execute(
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS similarity_score FLOAT;"
        )
        print("[OK] similarity_score column added (or already existed).")

        # 4. Add index on is_duplicate for fast filtering
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE tablename = 'articles'
                    AND   indexname = 'idx_articles_is_duplicate'
                ) THEN
                    CREATE INDEX idx_articles_is_duplicate ON articles(is_duplicate);
                END IF;
            END $$;
            """
        )
        print("[OK] idx_articles_is_duplicate index ensured.")

        # 5. Add index on duplicate_of_id for fast duplicate lookup
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE tablename = 'articles'
                    AND   indexname = 'idx_articles_duplicate_of_id'
                ) THEN
                    CREATE INDEX idx_articles_duplicate_of_id ON articles(duplicate_of_id);
                END IF;
            END $$;
            """
        )
        print("[OK] idx_articles_duplicate_of_id index ensured.")

    conn.commit()
    print("\n[OK] Migration complete -- duplicate tracking columns are ready.\n")

except Exception as exc:
    conn.rollback()
    print(f"\n[FAIL] Migration failed: {exc}\n")
    raise
finally:
    release_connection(conn)
