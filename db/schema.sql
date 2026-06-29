-- db/schema.sql
-- Base DDL for the RSS Scraping Pipeline database.
-- Always executed on startup via db/connection.py → init_schema().
-- Does NOT include pgvector — that lives in schema_vector.sql.

-- ─────────────────────────────────────────────────────────────────────────────
-- Extension: enable case-insensitive text
-- ─────────────────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS citext;

-- ─────────────────────────────────────────────────────────────────────────────
-- Table: rss_sources
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rss_sources (
    id          SERIAL PRIMARY KEY,
    name        TEXT        NOT NULL,
    url         CITEXT      UNIQUE NOT NULL,
    category    TEXT        NOT NULL DEFAULT 'General',
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_fetched_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_rss_sources_category ON rss_sources(category);

-- ─────────────────────────────────────────────────────────────────────────────
-- Table: articles
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS articles (
    id           SERIAL PRIMARY KEY,
    source_id    INT         REFERENCES rss_sources(id) ON DELETE SET NULL,
    title        TEXT        NOT NULL,
    url          CITEXT      UNIQUE NOT NULL,
    author       TEXT,
    published_at TIMESTAMPTZ,
    summary      TEXT,
    full_text    TEXT,
    word_count   INT,
    language     TEXT        DEFAULT 'en',
    is_clean     BOOLEAN     NOT NULL DEFAULT FALSE,
    scraped_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Duplicate tracking (content-only semantic deduplication)
    is_duplicate     BOOLEAN     NOT NULL DEFAULT FALSE,
    duplicate_of_id  INT         REFERENCES articles(id) ON DELETE SET NULL,
    similarity_score FLOAT
    -- NOTE: embedding VECTOR(384) column added by schema_vector.sql when pgvector is available
);

CREATE INDEX IF NOT EXISTS idx_articles_source_id    ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_language     ON articles(language);
CREATE INDEX IF NOT EXISTS idx_articles_is_clean        ON articles(is_clean);
CREATE INDEX IF NOT EXISTS idx_articles_is_duplicate    ON articles(is_duplicate);
CREATE INDEX IF NOT EXISTS idx_articles_duplicate_of_id ON articles(duplicate_of_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Table: pipeline_runs
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id                SERIAL PRIMARY KEY,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at       TIMESTAMPTZ,
    articles_found    INT         NOT NULL DEFAULT 0,
    articles_inserted INT         NOT NULL DEFAULT 0,
    articles_skipped  INT         NOT NULL DEFAULT 0,
    errors            INT         NOT NULL DEFAULT 0,
    status            TEXT        NOT NULL DEFAULT 'running'  -- running | success | partial | failed
);
