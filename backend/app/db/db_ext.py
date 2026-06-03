from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import settings

_pool: AsyncConnectionPool | None = None
_table_schema: str | None = None


async def open_pool() -> None:
    """Создаёт пул соединений с PostgreSQL.

    Стиль намеренно простой: один conninfo из .env и один общий pool на приложение.
    """
    global _pool
    if _pool is not None:
        return

    _pool = AsyncConnectionPool(
        conninfo=settings.pg_conninfo,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        open=False,
        kwargs={"row_factory": dict_row},
    )
    await _pool.open()


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_conn() -> AsyncIterator[AsyncConnection]:
    if _pool is None:
        await open_pool()
    assert _pool is not None
    async with _pool.connection() as conn:
        yield conn


async def resolve_news_schema() -> str:
    """Находит схему таблицы news_list.

    В прошлой версии была жёсткая ссылка на public.news_list. Из-за этого у вас
    падал backend, если подключение шло не к той БД или таблица лежит не в public.
    """
    global _table_schema

    if settings.news_schema:
        _table_schema = settings.news_schema
        return settings.news_schema

    if _table_schema:
        return _table_schema

    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT table_schema
                FROM information_schema.tables
                WHERE table_name = %s
                  AND table_type = 'BASE TABLE'
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY CASE WHEN table_schema = 'public' THEN 0 ELSE 1 END, table_schema
                LIMIT 1
                """,
                (settings.news_table,),
            )
            row = await cur.fetchone()

    if not row:
        raise RuntimeError(
            f"Таблица {settings.news_table!r} не найдена в текущей базе. "
            "Проверьте PG_CONNINFO: dbname, user, host, port. "
            "Если таблица в нестандартной схеме — укажите NEWS_SCHEMA."
        )

    _table_schema = row["table_schema"]
    return _table_schema


async def news_table_identifier() -> sql.Composed:
    schema = await resolve_news_schema()
    return sql.Identifier(schema, settings.news_table)
