-- db/schema_vector.sql
-- Optional DDL for semantic deduplication via pgvector.
-- Executed separately by init_schema() ONLY when the pgvector extension is
-- available on the PostgreSQL server. Failure here is caught gracefully and
-- the pipeline falls back to URL-only deduplication.
--
-- To install pgvector on your PostgreSQL server:
--   Windows (EDB installer): https://github.com/pgvector/pgvector#windows
--   Linux/Docker:            https://github.com/pgvector/pgvector#linux
--
-- After installing the server-side extension, restart the pipeline and
-- this file will be applied automatically.

-- ─────────────────────────────────────────────────────────────────────────────
-- Enable the vector extension on this database
-- ─────────────────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS vector;

-- ─────────────────────────────────────────────────────────────────────────────
-- Add the embedding column to articles (idempotent ADD COLUMN IF NOT EXISTS)
-- 384 dimensions matches all-MiniLM-L6-v2. Adjust if you change VECTOR_MODEL_NAME.
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE articles
    ADD COLUMN IF NOT EXISTS embedding VECTOR(384);

-- ─────────────────────────────────────────────────────────────────────────────
-- Content-only embedding column for source-agnostic semantic deduplication.
-- Stores an embedding of ONLY the article body text (full_text / content),
-- ignoring title, category, URL, and source metadata. This catches the same
-- news story reported by different sources with different headlines.
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE articles
    ADD COLUMN IF NOT EXISTS content_embedding VECTOR(384);

-- ─────────────────────────────────────────────────────────────────────────────
-- Add the source_embedding column to rss_sources for semantic source dedup.
-- Stores an embedding of (name + url + category) so that adding a new RSS
-- source whose meaning is already represented is blocked at the CLI layer.
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE rss_sources
    ADD COLUMN IF NOT EXISTS source_embedding VECTOR(384);

-- ─────────────────────────────────────────────────────────────────────────────
-- IVFFlat index for fast approximate cosine nearest-neighbour search.
-- lists=100 suits up to ~1 M rows; increase proportionally for larger datasets.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_articles_embedding
    ON articles USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- IVFFlat index for fast content-only cosine similarity search.
CREATE INDEX IF NOT EXISTS idx_articles_content_embedding
    ON articles USING ivfflat (content_embedding vector_cosine_ops)
    WITH (lists = 100);

-- IVFFlat index for fast source-level cosine similarity search.
CREATE INDEX IF NOT EXISTS idx_rss_sources_embedding
    ON rss_sources USING ivfflat (source_embedding vector_cosine_ops)
    WITH (lists = 10);

