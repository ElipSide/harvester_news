from __future__ import annotations

from datetime import datetime, time, timedelta, date
from typing import Any

from psycopg import sql

from app.config import settings
from app.db.db_ext import get_conn, news_table_identifier
from app.db.db_ext_func import fetch_one, fetch_val
from app.services_news import QUALITY_WHERE


def topic_marks_table_identifier() -> sql.Composed:
    return sql.Identifier(settings.events_schema, "news_topic_marks")


def topic_state_table_identifier() -> sql.Composed:
    return sql.Identifier(settings.events_schema, "news_topic_index_state")


def topic_daily_stats_table_identifier() -> sql.Composed:
    return sql.Identifier(settings.events_schema, "topic_daily_stats")


def topic_daily_totals_table_identifier() -> sql.Composed:
    return sql.Identifier(settings.events_schema, "topic_daily_totals")


def normalize_topic(value: str) -> str:
    return (value or "").strip().casefold().replace("ё", "е")


async def ensure_topic_index_schema() -> None:
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(settings.events_schema)))
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
                ).format(sql.Identifier(settings.events_schema))
            )
            # Отдельная таблица состояния нужна, чтобы помечать обработанными даже новости БЕЗ topics.
            # Иначе --sync-topics --drain бесконечно выбирает одни и те же 1000 строк без тем.
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
                ).format(sql.Identifier(settings.events_schema))
            )
            # Готовые дневные агрегаты для графика.
            # UI читает эти маленькие таблицы вместо пересчёта news_topic_marks/news_list на каждом открытии.
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
                ).format(sql.Identifier(settings.events_schema))
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
                ).format(sql.Identifier(settings.events_schema))
            )
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_news_topics_topic_date_idx ON {}.news_topic_marks (topic_norm, news_date DESC, news_id)").format(sql.Identifier(settings.events_schema)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_news_topics_date_idx ON {}.news_topic_marks (news_date DESC, news_id)").format(sql.Identifier(settings.events_schema)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_news_topics_topic_name_idx ON {}.news_topic_marks (topic, topic_norm)").format(sql.Identifier(settings.events_schema)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_news_topic_state_date_idx ON {}.news_topic_index_state (news_date DESC, news_id)").format(sql.Identifier(settings.events_schema)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_news_topic_state_hash_idx ON {}.news_topic_index_state (topics_hash, news_id)").format(sql.Identifier(settings.events_schema)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_topic_daily_stats_date_idx ON {}.topic_daily_stats (bucket_date DESC)").format(sql.Identifier(settings.events_schema)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_topic_daily_stats_topic_date_idx ON {}.topic_daily_stats (topic_norm, bucket_date DESC)").format(sql.Identifier(settings.events_schema)))
            await cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS hn_topic_daily_totals_date_idx ON {}.topic_daily_totals (bucket_date DESC)").format(sql.Identifier(settings.events_schema)))

        await conn.commit()


async def topic_index_count() -> int:
    exists = await fetch_val("SELECT to_regclass(%(table_name)s)::text", {"table_name": f"{settings.events_schema}.news_topic_marks"})
    if not exists:
        return 0
    return int(await fetch_val(sql.SQL("SELECT COUNT(*)::int FROM {}").format(topic_marks_table_identifier())) or 0)


async def topic_index_stats() -> dict[str, Any]:
    await ensure_topic_index_schema()
    row = await fetch_one(
        sql.SQL(
            """
            SELECT
                (SELECT COUNT(*)::int FROM {marks}) AS rows,
                (SELECT COUNT(DISTINCT news_id)::int FROM {marks}) AS news_indexed_with_topics,
                (SELECT COUNT(DISTINCT topic_norm)::int FROM {marks}) AS topics,
                (SELECT MAX(news_date)::text FROM {marks}) AS max_date,
                (SELECT MIN(news_date)::text FROM {marks}) AS min_date,
                (SELECT COUNT(*)::int FROM {state}) AS news_seen,
                (SELECT COUNT(*)::int FROM {state} WHERE topic_count = 0) AS news_without_topics,
                (SELECT COUNT(*)::int FROM {daily_stats}) AS daily_stat_rows,
                (SELECT COUNT(*)::int FROM {daily_totals}) AS daily_total_rows
            """
        ).format(
            marks=topic_marks_table_identifier(),
            state=topic_state_table_identifier(),
            daily_stats=topic_daily_stats_table_identifier(),
            daily_totals=topic_daily_totals_table_identifier(),
        )
    )
    return row or {
        "rows": 0,
        "news_indexed_with_topics": 0,
        "topics": 0,
        "max_date": None,
        "min_date": None,
        "news_seen": 0,
        "news_without_topics": 0,
        "daily_stat_rows": 0,
        "daily_total_rows": 0,
    }


async def refresh_topic_daily_stats_for_dates(dates: list[date]) -> dict[str, Any]:
    """Пересчитывает агрегаты графика только для затронутых дат."""
    clean_dates = sorted({d for d in dates if isinstance(d, date)})
    if not clean_dates:
        return {"status": "ok", "dates": 0, "daily_stat_rows": 0, "daily_total_rows": 0}

    await ensure_topic_index_schema()
    marks = topic_marks_table_identifier()
    state = topic_state_table_identifier()
    daily_stats = topic_daily_stats_table_identifier()
    daily_totals = topic_daily_totals_table_identifier()
    params = {"dates": clean_dates}

    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql.SQL("DELETE FROM {stats} WHERE bucket_date = ANY(%(dates)s)").format(stats=daily_stats), params)
            await cur.execute(sql.SQL("DELETE FROM {totals} WHERE bucket_date = ANY(%(dates)s)").format(totals=daily_totals), params)
            await cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {stats} (bucket_date, topic, topic_norm, news_count, updated_at)
                    SELECT
                        news_date AS bucket_date,
                        topic,
                        topic_norm,
                        COUNT(DISTINCT news_id)::int AS news_count,
                        CURRENT_TIMESTAMP
                    FROM {marks}
                    WHERE news_date = ANY(%(dates)s)
                      AND NULLIF(BTRIM(topic), '') IS NOT NULL
                    GROUP BY news_date, topic, topic_norm
                    ON CONFLICT (bucket_date, topic_norm) DO UPDATE SET
                        topic = EXCLUDED.topic,
                        news_count = EXCLUDED.news_count,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ).format(stats=daily_stats, marks=marks),
                params,
            )
            await cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {totals} (bucket_date, news_count, updated_at)
                    SELECT
                        news_date AS bucket_date,
                        COUNT(*)::int AS news_count,
                        CURRENT_TIMESTAMP
                    FROM {state}
                    WHERE news_date = ANY(%(dates)s)
                    GROUP BY news_date
                    ON CONFLICT (bucket_date) DO UPDATE SET
                        news_count = EXCLUDED.news_count,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ).format(totals=daily_totals, state=state),
                params,
            )
        await conn.commit()

    row = await fetch_one(
        sql.SQL(
            """
            SELECT
                (SELECT COUNT(*)::int FROM {daily_stats} WHERE bucket_date = ANY(%(dates)s)) AS daily_stat_rows,
                (SELECT COUNT(*)::int FROM {daily_totals} WHERE bucket_date = ANY(%(dates)s)) AS daily_total_rows
            """
        ).format(daily_stats=daily_stats, daily_totals=daily_totals),
        params,
    )
    return {
        "status": "ok",
        "dates": len(clean_dates),
        "daily_stat_rows": int((row or {}).get("daily_stat_rows") or 0),
        "daily_total_rows": int((row or {}).get("daily_total_rows") or 0),
    }


async def rebuild_topic_daily_stats() -> dict[str, Any]:
    """Полностью пересобирает готовые дневные агрегаты графика.

    Таблицы маленькие: примерно количество дней * количество тем.
    Зато /timeline после этого не делает JOIN с news_list и не группирует
    десятки/сотни тысяч строк на каждый запрос страницы.
    """
    await ensure_topic_index_schema()
    marks = topic_marks_table_identifier()
    state = topic_state_table_identifier()
    daily_stats = topic_daily_stats_table_identifier()
    daily_totals = topic_daily_totals_table_identifier()

    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql.SQL("TRUNCATE {}, {} RESTART IDENTITY").format(daily_stats, daily_totals))
            await cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {daily_stats} (bucket_date, topic, topic_norm, news_count, updated_at)
                    SELECT
                        news_date AS bucket_date,
                        topic,
                        topic_norm,
                        COUNT(DISTINCT news_id)::int AS news_count,
                        CURRENT_TIMESTAMP
                    FROM {marks}
                    WHERE news_date IS NOT NULL
                      AND NULLIF(BTRIM(topic), '') IS NOT NULL
                    GROUP BY news_date, topic, topic_norm
                    """
                ).format(daily_stats=daily_stats, marks=marks)
            )
            await cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {daily_totals} (bucket_date, news_count, updated_at)
                    SELECT
                        news_date AS bucket_date,
                        COUNT(*)::int AS news_count,
                        CURRENT_TIMESTAMP
                    FROM {state}
                    WHERE news_date IS NOT NULL
                    GROUP BY news_date
                    """
                ).format(daily_totals=daily_totals, state=state)
            )
        await conn.commit()

    row = await fetch_one(
        sql.SQL(
            """
            SELECT
                (SELECT COUNT(*)::int FROM {daily_stats}) AS daily_stat_rows,
                (SELECT COUNT(*)::int FROM {daily_totals}) AS daily_total_rows,
                (SELECT MIN(bucket_date)::text FROM {daily_totals}) AS min_date,
                (SELECT MAX(bucket_date)::text FROM {daily_totals}) AS max_date
            """
        ).format(daily_stats=daily_stats, daily_totals=daily_totals)
    )
    return {
        "status": "ok",
        "daily_stat_rows": int((row or {}).get("daily_stat_rows") or 0),
        "daily_total_rows": int((row or {}).get("daily_total_rows") or 0),
        "min_date": (row or {}).get("min_date"),
        "max_date": (row or {}).get("max_date"),
    }


async def reset_topic_index() -> None:
    await ensure_topic_index_schema()
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                sql.SQL("TRUNCATE {}, {}, {}, {} RESTART IDENTITY").format(
                    topic_marks_table_identifier(),
                    topic_state_table_identifier(),
                    topic_daily_stats_table_identifier(),
                    topic_daily_totals_table_identifier(),
                )
            )
        await conn.commit()


async def _latest_news_date(news_table: sql.Composable) -> date:
    value = await fetch_val(
        sql.SQL("SELECT MAX(n.date)::date FROM {table} n WHERE {quality}").format(table=news_table, quality=QUALITY_WHERE)
    )
    return value if isinstance(value, date) else date.today()


async def sync_topic_index_once(*, limit: int | None = None, lookback_days: int | None = None, process_all: bool = False) -> dict[str, Any]:
    """Индексирует темы из колонки news_list.topics в нормализованную таблицу.

    Берём только поле topics. Поддерживаются форматы:
    - ["Регулирование", "Экспорт"]
    - {"Регулирование": "", "Экспорт": ""}
    - "Россия"

    Важное отличие: отдельно ведём таблицу news_topic_index_state.
    Она помечает обработанными даже новости без topics, чтобы drain-режим
    не зацикливался на одних и тех же строках с topic_rows=0.
    """
    await ensure_topic_index_schema()
    news_table = await news_table_identifier()
    marks = topic_marks_table_identifier()
    state = topic_state_table_identifier()

    params: dict[str, Any] = {"limit": int(limit or settings.event_worker_fetch_limit)}
    date_sql = sql.SQL("")
    if not process_all:
        anchor = await _latest_news_date(news_table)
        days = int(lookback_days or settings.event_worker_lookback_days)
        params["date_from"] = datetime.combine(anchor - timedelta(days=days), time.min)
        date_sql = sql.SQL(" AND n.date >= %(date_from)s")

    # Выбираем новости, которые ещё не видели, или у которых изменился JSON topics.
    # md5(coalesce(n.topics::text,'')) дешёвый и позволяет переиндексировать изменённые topics.
    selected_sql = (
        sql.SQL(
            """
            SELECT
                n.id,
                n.date::date AS news_date,
                n.topics,
                md5(COALESCE(n.topics::text, '')) AS topics_hash
            FROM 
            """
        )
        + news_table
        + sql.SQL(
            """
             n
            LEFT JOIN 
            """
        )
        + state
        + sql.SQL(
            """
             st ON st.news_id = n.id
            WHERE 
            """
        )
        + QUALITY_WHERE
        + date_sql
        + sql.SQL(
            """
              AND (st.news_id IS NULL OR st.topics_hash <> md5(COALESCE(n.topics::text, '')))
            ORDER BY n.date DESC NULLS LAST, n.id DESC
            LIMIT %(limit)s
            """
        )
    )

    query = (
        sql.SQL("WITH selected AS MATERIALIZED (")
        + selected_sql
        + sql.SQL(
            """
            ), expanded AS (
                SELECT id, news_date, elem.value AS topic
                FROM selected s
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    CASE WHEN jsonb_typeof(s.topics) = 'array' THEN s.topics ELSE '[]'::jsonb END
                ) AS elem(value)

                UNION ALL

                SELECT id, news_date, obj.key AS topic
                FROM selected s
                CROSS JOIN LATERAL jsonb_each_text(
                    CASE WHEN jsonb_typeof(s.topics) = 'object' THEN s.topics ELSE '{}'::jsonb END
                ) AS obj(key, value)

                UNION ALL

                SELECT id, news_date, s.topics #>> '{}' AS topic
                FROM selected s
                WHERE jsonb_typeof(s.topics) = 'string'
            ), clean AS MATERIALIZED (
                SELECT DISTINCT
                       id AS news_id,
                       BTRIM(topic) AS topic,
                       REPLACE(LOWER(BTRIM(topic)), 'ё', 'е') AS topic_norm,
                       news_date
                FROM expanded
                WHERE NULLIF(BTRIM(topic), '') IS NOT NULL
                  AND LOWER(BTRIM(topic)) NOT IN ('null', 'none', 'undefined', 'true', 'false')
                  AND NOT (BTRIM(topic) ~* '^(https?://|www[.]|t[.]me/)')
            """
        )
        + sql.SQL(
            """
            ), deleted_old_marks AS (
                DELETE FROM 
            """
        )
        + marks
        + sql.SQL(
            """
                 m USING selected s
                WHERE m.news_id = s.id
                RETURNING m.news_id
            ), inserted_marks AS (
                INSERT INTO 
            """
        )
        + marks
        + sql.SQL(
            """
                 (news_id, topic, topic_norm, news_date, updated_at)
                SELECT news_id, topic, topic_norm, news_date, CURRENT_TIMESTAMP
                FROM clean
                ON CONFLICT (news_id, topic_norm) DO UPDATE SET
                    topic = EXCLUDED.topic,
                    news_date = EXCLUDED.news_date,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING news_id, topic_norm
            ), topic_counts AS (
                SELECT news_id, COUNT(*)::int AS topic_count
                FROM clean
                GROUP BY news_id
            ), state_upsert AS (
                INSERT INTO 
            """
        )
        + state
        + sql.SQL(
            """
                 (news_id, news_date, topics_hash, topic_count, updated_at)
                SELECT
                    s.id,
                    s.news_date,
                    s.topics_hash,
                    COALESCE(tc.topic_count, 0),
                    CURRENT_TIMESTAMP
                FROM selected s
                LEFT JOIN topic_counts tc ON tc.news_id = s.id
                ON CONFLICT (news_id) DO UPDATE SET
                    news_date = EXCLUDED.news_date,
                    topics_hash = EXCLUDED.topics_hash,
                    topic_count = EXCLUDED.topic_count,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING news_id, topic_count
            )
            SELECT
                (SELECT COUNT(*)::int FROM selected) AS selected_news,
                (SELECT COUNT(*)::int FROM inserted_marks) AS topic_rows,
                (SELECT COUNT(DISTINCT news_id)::int FROM inserted_marks) AS news_indexed,
                (SELECT COUNT(*)::int FROM state_upsert) AS news_seen,
                (SELECT COUNT(*)::int FROM state_upsert WHERE topic_count = 0) AS news_without_topics,
                (SELECT COALESCE(array_agg(DISTINCT news_date::text), ARRAY[]::text[]) FROM selected WHERE news_date IS NOT NULL) AS affected_dates
            """
        )
    )

    row = await fetch_one(query, params)
    selected_news = int((row or {}).get("selected_news") or 0)

    # Если появились новые/изменённые новости, обновляем готовые агрегаты только для затронутых дней.
    # Это выполняется в worker-е, а не в пользовательском запросе /timeline.
    daily_stats: dict[str, Any] | None = None
    if selected_news > 0:
        affected_dates_raw = (row or {}).get("affected_dates") or []
        affected_dates: list[date] = []
        for raw in affected_dates_raw:
            if isinstance(raw, date):
                affected_dates.append(raw)
            else:
                try:
                    affected_dates.append(datetime.strptime(str(raw), "%Y-%m-%d").date())
                except ValueError:
                    continue
        daily_stats = await refresh_topic_daily_stats_for_dates(affected_dates)

    result = {
        "status": "ok",
        "selected_news": selected_news,
        "topic_rows": int((row or {}).get("topic_rows") or 0),
        "news_indexed": int((row or {}).get("news_indexed") or 0),
        "news_seen": int((row or {}).get("news_seen") or 0),
        "news_without_topics": int((row or {}).get("news_without_topics") or 0),
    }
    if daily_stats is not None:
        result["daily_stats"] = daily_stats
    return result
