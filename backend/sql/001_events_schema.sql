-- Постоянный слой MVP-событий для Harvester News.
-- По умолчанию backend создаёт эти таблицы автоматически в схеме EVENTS_SCHEMA.
-- Этот файл можно применить вручную, если нужно:
--   psql "$PG_CONNINFO" -f backend/sql/001_events_schema.sql

CREATE SCHEMA IF NOT EXISTS harvester_news;

CREATE TABLE IF NOT EXISTS harvester_news.events (
    id BIGSERIAL PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    sigma INTEGER NOT NULL DEFAULT 50,
    news_count INTEGER NOT NULL DEFAULT 0,
    sources_count INTEGER NOT NULL DEFAULT 0,
    views INTEGER NOT NULL DEFAULT 0,
    date_from TIMESTAMP WITHOUT TIME ZONE,
    date_to TIMESTAMP WITHOUT TIME ZONE,
    main_news_id INTEGER,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    topics JSONB NOT NULL DEFAULT '[]'::jsonb,
    regions JSONB NOT NULL DEFAULT '[]'::jsonb,
    products JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_llm JSONB, -- техническое поле: offline-RAG/evidence metadata, имя оставлено для совместимости
    model TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP WITHOUT TIME ZONE,
    last_seen_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS harvester_news.event_sources (
    event_id BIGINT NOT NULL REFERENCES harvester_news.events(id) ON DELETE CASCADE,
    news_id INTEGER NOT NULL,
    news_date TIMESTAMP WITHOUT TIME ZONE,
    title TEXT,
    source TEXT,
    customer TEXT,
    link_site TEXT,
    snippet TEXT,
    views INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(event_id, news_id)
);

CREATE TABLE IF NOT EXISTS harvester_news.event_impacts (
    event_id BIGINT NOT NULL REFERENCES harvester_news.events(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    label TEXT NOT NULL,
    impact TEXT NOT NULL CHECK (impact IN ('positive', 'negative', 'neutral', 'watch')),
    summary TEXT NOT NULL DEFAULT '',
    action_hint TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(event_id, role)
);

CREATE TABLE IF NOT EXISTS harvester_news.event_job_state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS hn_events_date_idx ON harvester_news.events (date_to DESC NULLS LAST, id DESC);
CREATE INDEX IF NOT EXISTS hn_events_status_idx ON harvester_news.events (status);
CREATE INDEX IF NOT EXISTS hn_event_sources_news_idx ON harvester_news.event_sources (news_id);
CREATE INDEX IF NOT EXISTS hn_event_sources_event_idx ON harvester_news.event_sources (event_id);
CREATE INDEX IF NOT EXISTS hn_event_impacts_role_idx ON harvester_news.event_impacts (role);
