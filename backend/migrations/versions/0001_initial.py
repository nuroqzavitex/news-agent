"""initial schema (spec §7)

Revision ID: 0001
Revises:
Create Date: 2026-11-01
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- unaccent() không IMMUTABLE nên không dùng được trong generated column → bọc lại.
CREATE OR REPLACE FUNCTION immutable_unaccent(text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
    AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$;

CREATE TABLE sources (
    id              SERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    rss_url         TEXT NOT NULL,
    category        TEXT,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    last_crawled_at TIMESTAMPTZ,
    last_status     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE articles (
    id              BIGSERIAL PRIMARY KEY,
    source_id       INT NOT NULL REFERENCES sources(id),
    url             TEXT NOT NULL UNIQUE,
    url_hash        TEXT NOT NULL,
    title           TEXT NOT NULL,
    summary         TEXT,
    content         TEXT,
    author          TEXT,
    category        TEXT,
    published_at    TIMESTAMPTZ,
    crawled_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    simhash         BIGINT,
    duplicate_of    BIGINT REFERENCES articles(id),
    sentiment_label TEXT,
    sentiment_score REAL,
    tsv             TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', immutable_unaccent(coalesce(title, ''))), 'A') ||
        setweight(to_tsvector('simple', immutable_unaccent(coalesce(summary, ''))), 'B') ||
        setweight(to_tsvector('simple', immutable_unaccent(coalesce(content, ''))), 'C')
    ) STORED,
    lang            TEXT NOT NULL DEFAULT 'vi',
    status          TEXT NOT NULL DEFAULT 'processed'
);
CREATE INDEX articles_url_hash_idx ON articles (url_hash);
CREATE INDEX articles_tsv_idx ON articles USING GIN (tsv);
CREATE INDEX articles_published_idx ON articles (published_at DESC);
CREATE INDEX articles_source_published_idx ON articles (source_id, published_at DESC);

CREATE TABLE article_chunks (
    id          BIGSERIAL PRIMARY KEY,
    article_id  BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    text        TEXT NOT NULL,
    embedding   vector(768),
    UNIQUE (article_id, chunk_index)
);
CREATE INDEX article_chunks_embedding_idx ON article_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE knowledge_items (
    id                BIGSERIAL PRIMARY KEY,
    key               TEXT NOT NULL,
    content           JSONB NOT NULL,
    source_agent      TEXT NOT NULL,
    source_message_id TEXT,
    version           INT NOT NULL DEFAULT 1,
    confidence        REAL NOT NULL DEFAULT 0.5,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ,
    superseded_by     BIGINT REFERENCES knowledge_items(id),
    conflict_group    UUID
);
CREATE INDEX knowledge_items_key_idx ON knowledge_items (key);

CREATE TABLE messages (
    id              BIGSERIAL PRIMARY KEY,
    message_id      TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL,
    trace_id        TEXT NOT NULL,
    direction       TEXT NOT NULL,
    from_agent      TEXT NOT NULL,
    to_agent        TEXT NOT NULL,
    type            TEXT NOT NULL,
    capability      TEXT,
    reply_to        TEXT,
    payload         JSONB NOT NULL,
    status          TEXT NOT NULL,
    attempts        INT NOT NULL DEFAULT 1,
    latency_ms      INT,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX messages_trace_idx ON messages (trace_id);
CREATE INDEX messages_created_idx ON messages (created_at DESC);

CREATE TABLE trace_events (
    id         BIGSERIAL PRIMARY KEY,
    trace_id   TEXT NOT NULL,
    message_id TEXT,
    agent_id   TEXT NOT NULL,
    event      TEXT NOT NULL,
    attrs      JSONB NOT NULL DEFAULT '{}',
    ts         TIMESTAMPTZ NOT NULL
);
CREATE INDEX trace_events_trace_idx ON trace_events (trace_id, ts);

CREATE TABLE agent_cache (
    agent_id     TEXT PRIMARY KEY,
    card         JSONB NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source       TEXT NOT NULL
);
"""

DOWNGRADE = """
DROP TABLE IF EXISTS agent_cache, trace_events, messages, knowledge_items, article_chunks, articles, sources;
DROP FUNCTION IF EXISTS immutable_unaccent(text);
"""


def _run(script: str) -> None:
    # asyncpg dùng prepared statement → mỗi lần execute chỉ được 1 câu lệnh.
    for statement in script.split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE)


def downgrade() -> None:
    _run(DOWNGRADE)
