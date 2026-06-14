-- db/schema.sql
-- Full DDL for the RSS Scraping Pipeline database.
-- Run automatically on startup via db/connection.py → init_schema().

-- ─────────────────────────────────────────────────────────────────────────────
-- Extension: enable case-insensitive text (optional but useful)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS citext;

-- ─────────────────────────────────────────────────────────────────────────────
-- Table: rss_sources
-- Stores the list of configured RSS feed sources.
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
-- Stores every scraped and cleaned article.
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
    scraped_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_articles_source_id   ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_language     ON articles(language);
CREATE INDEX IF NOT EXISTS idx_articles_is_clean     ON articles(is_clean);

-- ─────────────────────────────────────────────────────────────────────────────
-- Table: pipeline_runs
-- Audit log for every scheduler execution.
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
