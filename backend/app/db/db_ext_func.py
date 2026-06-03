from __future__ import annotations

from typing import Any, Mapping, Sequence

from psycopg import sql

from app.db.db_ext import get_conn


async def fetch_all(query: str | sql.Composable, params: Mapping[str, Any] | Sequence[Any] | None = None) -> list[dict[str, Any]]:
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, params or {})
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def fetch_one(query: str | sql.Composable, params: Mapping[str, Any] | Sequence[Any] | None = None) -> dict[str, Any] | None:
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, params or {})
            row = await cur.fetchone()
            return dict(row) if row else None


async def fetch_val(query: str | sql.Composable, params: Mapping[str, Any] | Sequence[Any] | None = None) -> Any:
    row = await fetch_one(query, params)
    if not row:
        return None
    return next(iter(row.values()))


async def execute(query: str | sql.Composable, params: Mapping[str, Any] | Sequence[Any] | None = None) -> None:
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, params or {})
        await conn.commit()
