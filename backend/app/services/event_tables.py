from __future__ import annotations

from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

from app.config import settings
from app.db.db_ext import get_conn
from app.db.db_ext_func import fetch_all, fetch_one, fetch_val


def event_schema_identifier() -> sql.Identifier:
    return sql.Identifier(settings.events_schema)


def event_table_identifier(table: str) -> sql.Composed:
    return sql.Identifier(settings.events_schema, table)


async def ensure_event_schema() -> None:
    """Создаёт постоянные таблицы MVP-событий.

    Таблицы живут в отдельной схеме `EVENTS_SCHEMA` и не конфликтуют с уже
    существующей `news_list`. Backend и worker вызывают эту функцию безопасно
    при старте.
    """
    schema_name = settings.events_schema
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema_name)))

            await cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.events (
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
                        raw_llm JSONB,
                        model TEXT,
                        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        processed_at TIMESTAMP WITHOUT TIME ZONE,
                        last_seen_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                ).format(sql.Identifier(schema_name))
            )

            await cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.event_sources (
                        event_id BIGINT NOT NULL REFERENCES {}.events(id) ON DELETE CASCADE,
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
                    )
                    """
                ).format(sql.Identifier(schema_name), sql.Identifier(schema_name))
            )

            await cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.event_impacts (
                        event_id BIGINT NOT NULL REFERENCES {}.events(id) ON DELETE CASCADE,
                        role TEXT NOT NULL,
                        label TEXT NOT NULL,
                        impact TEXT NOT NULL CHECK (impact IN ('positive', 'negative', 'neutral', 'watch')),
                        summary TEXT NOT NULL DEFAULT '',
                        action_hint TEXT NOT NULL DEFAULT '',
                        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(event_id, role)
                    )
                    """
                ).format(sql.Identifier(schema_name), sql.Identifier(schema_name))
            )

            await cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.event_job_state (
                        key TEXT PRIMARY KEY,
                        value JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                ).format(sql.Identifier(schema_name))
            )

            await cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.event_news_state (
                        news_id INTEGER PRIMARY KEY,
                        news_date TIMESTAMP WITHOUT TIME ZONE,
                        status TEXT NOT NULL DEFAULT 'seen',
                        reason TEXT NOT NULL DEFAULT '',
                        processed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                ).format(sql.Identifier(schema_name))
            )

            await cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.news_topic_marks (
                        news_id INTEGER NOT NULL,
                        topic TEXT NOT NULL,
                        topic_norm TEXT NOT NULL,
                        news_date DATE,
                        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (news_id, topic_norm)
                    )
                    """
                ).format(sql.Identifier(schema_name))
            )

            await cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.news_topic_index_state (
                        news_id INTEGER PRIMARY KEY,
                        news_date DATE,
                        topics_hash TEXT NOT NULL DEFAULT '',
                        topic_count INTEGER NOT NULL DEFAULT 0,
                        indexed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                ).format(sql.Identifier(schema_name))
            )

            await cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.topic_daily_stats (
                        bucket_date DATE NOT NULL,
                        topic TEXT NOT NULL,
                        topic_norm TEXT NOT NULL,
                        news_count INTEGER NOT NULL DEFAULT 0,
                        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (bucket_date, topic_norm)
                    )
                    """
                ).format(sql.Identifier(schema_name))
            )

            await cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.topic_daily_totals (
                        bucket_date DATE PRIMARY KEY,
                        news_count INTEGER NOT NULL DEFAULT 0,
                        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                ).format(sql.Identifier(schema_name))
            )

            # ── Сюжетный граф: связи между событиями (рёбра) и сюжеты-деревья ──
            # Заполняются воркером (rebuild_event_graph) после сборки событий.
            await cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.event_stories (
                        id BIGSERIAL PRIMARY KEY,
                        story_key TEXT NOT NULL UNIQUE,
                        name TEXT NOT NULL DEFAULT '',
                        color TEXT NOT NULL DEFAULT '#1E4FB0',
                        size INTEGER NOT NULL DEFAULT 0,
                        date_from TIMESTAMP WITHOUT TIME ZONE,
                        date_to TIMESTAMP WITHOUT TIME ZONE,
                        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                ).format(sql.Identifier(schema_name))
            )

            await cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.event_links (
                        from_id BIGINT NOT NULL REFERENCES {}.events(id) ON DELETE CASCADE,
                        to_id BIGINT NOT NULL REFERENCES {}.events(id) ON DELETE CASCADE,
                        weight REAL NOT NULL DEFAULT 0,
                        channel TEXT NOT NULL DEFAULT 'T',
                        lab TEXT,
                        in_story BOOLEAN NOT NULL DEFAULT FALSE,
                        PRIMARY KEY(from_id, to_id)
                    )
                    """
                ).format(sql.Identifier(schema_name), sql.Identifier(schema_name), sql.Identifier(schema_name))
            )

            # ── Предрасчётная проекция для SVG-графа на главной ──
            # Одна строка на активное событие с уже очищенными гранями (TEXT[]) и
            # ключами недели/месяца. Заполняется воркером (rebuild_event_graph_rows),
            # читается эндпоинтом /events/graph без JSONB-парсинга на лету.
            await cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.event_graph_rows (
                        event_id BIGINT PRIMARY KEY REFERENCES {}.events(id) ON DELETE CASCADE,
                        date_from DATE,
                        date_to DATE,
                        week_key TEXT,
                        month_key TEXT,
                        topics TEXT[] NOT NULL DEFAULT '{{}}',
                        regions TEXT[] NOT NULL DEFAULT '{{}}',
                        products TEXT[] NOT NULL DEFAULT '{{}}',
                        sigma INTEGER NOT NULL DEFAULT 0,
                        sources_count INTEGER NOT NULL DEFAULT 0
                    )
                    """
                ).format(sql.Identifier(schema_name), sql.Identifier(schema_name))
            )

            # Привязка события к сюжету-дереву (≤1 сюжет на событие на практике).
            await cur.execute(sql.SQL("ALTER TABLE {}.events ADD COLUMN IF NOT EXISTS story_id BIGINT").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("ALTER TABLE {}.events ADD COLUMN IF NOT EXISTS story_parent_id BIGINT").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("ALTER TABLE {}.events ADD COLUMN IF NOT EXISTS story_pos INTEGER").format(sql.Identifier(schema_name)))

            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_events_date_idx ON {}.events (date_to DESC NULLS LAST, id DESC)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_events_status_idx ON {}.events (status)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_event_sources_news_idx ON {}.event_sources (news_id)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_event_sources_event_idx ON {}.event_sources (event_id)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_event_sources_event_date_idx ON {}.event_sources (event_id, news_date DESC)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_event_news_state_status_idx ON {}.event_news_state (status, news_date DESC)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_event_news_state_date_idx ON {}.event_news_state (news_date DESC, news_id)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_event_impacts_role_idx ON {}.event_impacts (role)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_news_topics_topic_date_idx ON {}.news_topic_marks (topic_norm, news_date DESC, news_id)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_news_topics_date_idx ON {}.news_topic_marks (news_date DESC, news_id)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_news_topics_news_idx ON {}.news_topic_marks (news_id)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_news_topic_state_date_idx ON {}.news_topic_index_state (news_date DESC, news_id)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_news_topic_state_hash_idx ON {}.news_topic_index_state (topics_hash, news_id)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_topic_daily_stats_date_idx ON {}.topic_daily_stats (bucket_date DESC)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_topic_daily_stats_topic_date_idx ON {}.topic_daily_stats (topic_norm, bucket_date DESC)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_topic_daily_totals_date_idx ON {}.topic_daily_totals (bucket_date DESC)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_event_links_from_idx ON {}.event_links (from_id)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_event_links_to_idx ON {}.event_links (to_id)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_events_story_idx ON {}.events (story_id)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_egr_sigma_idx ON {}.event_graph_rows (sigma DESC, sources_count DESC)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_egr_week_idx ON {}.event_graph_rows (week_key)").format(sql.Identifier(schema_name)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_egr_month_idx ON {}.event_graph_rows (month_key)").format(sql.Identifier(schema_name)))

        await conn.commit()


async def event_tables_exist() -> bool:
    row = await fetch_one(
        """
        SELECT 1 AS ok
        FROM information_schema.tables
        WHERE table_schema = %s AND table_name = 'events'
        LIMIT 1
        """,
        (settings.events_schema,),
    )
    return bool(row)


async def read_job_state(key: str) -> dict[str, Any] | None:
    if not await event_tables_exist():
        return None
    row = await fetch_one(
        sql.SQL("SELECT value FROM {} WHERE key = %(key)s").format(event_table_identifier("event_job_state")),
        {"key": key},
    )
    value = row.get("value") if row else None
    return dict(value) if isinstance(value, dict) else value


async def write_job_state(key: str, value: dict[str, Any]) -> None:
    await ensure_event_schema()
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {} (key, value, updated_at)
                    VALUES (%(key)s, %(value)s, CURRENT_TIMESTAMP)
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ).format(event_table_identifier("event_job_state")),
                {"key": key, "value": Jsonb(value)},
            )
        await conn.commit()
