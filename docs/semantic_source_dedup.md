# Semantic RSS Source Deduplication

## Overview

When adding a new RSS source via the CLI, the system now checks whether a
semantically similar source already exists in the database — using **vector
embeddings** and **cosine similarity** — before inserting.

- If no similar source exists → the new source is **added successfully**.
- If a similar source is found → the add is **rejected** with a clear message
  showing which existing source matched and the similarity score.

This prevents redundant sources (e.g. "TechCrunch" and "TechCrunch News Feed"
pointing to nearly the same feed) from accumulating in the pipeline.

---

## How It Works — End to End

```
python manage_sources.py add "TechCrunch News Feed" https://techcrunch.com/rss/ Technology
                │
                ▼
        1. Embed candidate text
           "name: TechCrunch News Feed url: https://... category: Technology"
           → 384-dimensional float vector
                │
                ▼
        2. Cosine similarity search (pgvector)
           SELECT id, name … 1 - (source_embedding <=> candidate) AS similarity
           FROM rss_sources WHERE source_embedding IS NOT NULL
           ORDER BY distance LIMIT 1
                │
                ▼
        3a. similarity ≥ threshold (0.92)?
            → raise SemanticDuplicateError
            → CLI prints rejection panel  ✗
                │
        3b. similarity < threshold?
            → INSERT new row into rss_sources
            → store embedding in source_embedding column
            → CLI prints success message  ✔
```

---

## Architecture

### Embedding Model

| Property | Value |
|---|---|
| Library | `sentence-transformers` |
| Model | `all-MiniLM-L6-v2` |
| Dimensions | 384 |
| Input | `"name: {name} url: {url} category: {category}"` |
| Normalised | Yes (`normalize_embeddings=True`) → cosine similarity = dot product |

The model is **lazy-loaded** once on first use and cached in memory (thread-safe
via `threading.Lock`). No API key is needed — everything runs locally.

### Similarity Metric

**Cosine similarity** is used. pgvector exposes it via the cosine *distance*
operator `<=>` where:

```
cosine_distance = 1 - cosine_similarity
```

So the query uses:

```sql
ORDER BY source_embedding <=> %s::vector   -- ascending distance = descending similarity
```

and the threshold check is:

```python
similarity = 1.0 - distance
if similarity >= VECTOR_SIM_THRESHOLD:     # duplicate → reject
```

### Threshold

Controlled by `VECTOR_SIM_THRESHOLD` in `config/settings.py` (default `0.92`),
overridable via `.env`:

```env
VECTOR_SIM_THRESHOLD=0.92   # default — rejects sources that are ≥ 92% similar
```

| Value | Effect |
|---|---|
| `0.95` | Stricter — only near-identical sources are blocked |
| `0.92` | Default — blocks clearly synonymous sources |
| `0.85` | More lenient — allows more variation between sources |

---

## Files Changed

### 1. `db/schema_vector.sql` — Database Schema

Added a `source_embedding` column and index to `rss_sources`:

```sql
-- New column on rss_sources
ALTER TABLE rss_sources
    ADD COLUMN IF NOT EXISTS source_embedding VECTOR(384);

-- IVFFlat index for fast cosine nearest-neighbour search
CREATE INDEX IF NOT EXISTS idx_rss_sources_embedding
    ON rss_sources USING ivfflat (source_embedding vector_cosine_ops)
    WITH (lists = 10);
```

The `articles` table already had `embedding VECTOR(384)` for article-level
dedup — this mirrors that pattern for sources.

---

### 2. `db/vector_store.py` — Two New Functions

#### `find_similar_source(embedding, conn, threshold) → dict | None`

Searches `rss_sources` for the nearest existing source embedding. Returns a
dict `{id, name, url, category, similarity}` if similarity ≥ threshold,
otherwise `None`.

```python
def find_similar_source(embedding, conn, threshold=0.92):
    cur.execute("""
        SELECT id, name, url, category,
               1.0 - (source_embedding <=> %s::vector) AS similarity
        FROM   rss_sources
        WHERE  source_embedding IS NOT NULL
        ORDER  BY source_embedding <=> %s::vector
        LIMIT  1
    """, (embedding, embedding))
    row = cur.fetchone()
    if row and float(row[4]) >= threshold:
        return {"id": row[0], "name": row[1], ..., "similarity": float(row[4])}
    return None
```

#### `store_source_embedding(source_id, embedding, conn) → None`

Persists the embedding for a newly inserted source so future adds can
deduplicate against it:

```python
def store_source_embedding(source_id, embedding, conn):
    cur.execute(
        "UPDATE rss_sources SET source_embedding = %s::vector WHERE id = %s",
        (embedding, source_id),
    )
```

Both functions gracefully return early (`None` / no-op) if pgvector is
unavailable, keeping the pipeline functional with URL-only dedup as fallback.

---

### 3. `db/connection.py` — `add_source()` and `SemanticDuplicateError`

#### `SemanticDuplicateError`

A custom exception that carries the matching source's details:

```python
class SemanticDuplicateError(Exception):
    def __init__(self, match: dict):
        self.match = match   # {id, name, url, category, similarity}
```

#### `add_source()` — Two-layer deduplication

```
Layer 1 — Semantic (vector):
  - Embed candidate text
  - find_similar_source() → if match → raise SemanticDuplicateError

Layer 2 — URL (always active):
  - INSERT … ON CONFLICT (url) DO NOTHING   ← catches exact URL duplicates
  - store_source_embedding() on success
```

The function gracefully skips Layer 1 if `is_vector_ready()` returns `False`
(sentence-transformers or pgvector not installed) and falls through to
URL-only dedup without raising an exception.

---

### 4. `manage_sources.py` — CLI User Experience

#### `cmd_add()` — Updated flow

```python
with console.status("[cyan]Checking for semantic duplicates…[/]"):
    try:
        new_id = add_source(name, url, category)

    except SemanticDuplicateError as dup:
        # Show rich red rejection panel with matching source details
        ...
        sys.exit(1)

    except Exception as exc:
        # Generic DB errors
        ...
        sys.exit(1)

# Only reached on success
console.print("✔  Source added successfully!")
```

**Rejection panel example:**
```
╭─ ✗  Duplicate Source Rejected ────────────────────────────╮
│ Source NOT added — semantic duplicate detected             │
│                                                            │
│   Existing source                                          │
│     ID       : 1                                           │
│     Name     : TechCrunch                                  │
│     URL      : https://techcrunch.com/feed/                │
│     Category : Technology                                  │
│                                                            │
│   Similarity score : 92.9%  (threshold: 92%)               │
│                                                            │
│   If you still want to add this source, first remove or    │
│   disable the existing one, or lower VECTOR_SIM_THRESHOLD. │
╰────────────────────────────────────────────────────────────╯
```

---

## One-Time Setup: pgvector Installation

pgvector must be installed server-side on PostgreSQL. Since PostgreSQL 18 on
Windows has no official prebuilt binary, the following steps were used:

1. Downloaded prebuilt binaries for PG18 from
   [`andreiramani/pgvector_pgsql_windows`](https://github.com/andreiramani/pgvector_pgsql_windows/releases)
   (`vector.v0.8.2-pg18.zip`)

2. Copied files as Administrator:
   - `vector.dll` → `C:\Program Files\PostgreSQL\18\lib\`
   - `vector--*.sql`, `vector.control` → `C:\Program Files\PostgreSQL\18\share\extension\`

3. Ran the migration script `apply_source_embedding_col.py` which:
   - Executed `CREATE EXTENSION IF NOT EXISTS vector;` in the database
   - Added `source_embedding VECTOR(384)` to `rss_sources`
   - Created the IVFFlat index
   - Backfilled embeddings for all 13 existing sources

---

## Backfill: Existing Sources

All sources that existed before this feature was added have no embedding yet.
The migration script backfills them:

```python
for source_id, name, url, category in existing_sources_without_embedding:
    text = f"name: {name} url: {url} category: {category}"
    emb  = embed_text(text)
    store_source_embedding(source_id, emb, conn)
```

Sources backfilled: TechCrunch, The Verge, Wired, Ars Technica, Reuters
Business, Yahoo Finance, MarketWatch, BBC News, Reuters Top News, NPR News,
NASA Breaking News, ScienceDaily, New Scientist.

---

## Graceful Fallback

If pgvector is not installed on the PostgreSQL server **or** `sentence-transformers`
is not installed in the Python environment:

- `is_vector_ready()` returns `False`
- `add_source()` skips Layer 1 entirely
- Exact URL deduplication (Layer 2) remains active
- No exception is raised — the pipeline continues normally

This ensures zero downtime on environments without vector support.

---

## Test

A smoke test at `test_semantic_source_dedup.py` verified:

| Case | Result |
|---|---|
| Adding "Archana's Kitchen" (food blog, genuinely new) | ✅ Added with ID 508 |
| Adding "TechCrunch News Feed" (near-duplicate) | ✅ Rejected (similarity = 0.9285) |
