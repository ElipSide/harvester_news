from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from psycopg import sql

from app.db.db_ext import news_table_identifier
from app.db.db_ext_func import fetch_val
from app.services_events import list_events
from app.services_news import _build_filters, featured_news, list_news, news_meta, timeline, top_read_news


async def _latest_week_bounds(
    *,
    q: str | None,
    topic: list[str],
    tag: list[str],
    region: str | None,
    product: str | None,
    source: str | None,
    has_photo: bool | None,
) -> tuple[date, date]:
    """Возвращает последнюю доступную неделю по данным, а не по календарю клиента.

    Так быстрый старт работает и на исторических выгрузках: если последняя новость в БД
    была неделю назад, берём неделю относительно неё, а не относительно сегодняшней даты.
    """
    table = await news_table_identifier()
    where, params = _build_filters(
        q=q,
        topics=topic,
        tags=tag,
        region=region,
        product=product,
        source=source,
        period=None,
        date_from=None,
        date_to=None,
        has_photo=has_photo,
    )
    value = await fetch_val(sql.SQL("SELECT MAX(n.date)::date FROM {table} n{where}").format(table=table, where=where), params)
    latest = value if isinstance(value, date) else date.today()
    return latest - timedelta(days=6), latest


async def home_initial_payload(
    *,
    q: str | None,
    topic: list[str],
    tag: list[str],
    region: str | None,
    product: str | None,
    source: str | None,
    has_photo: bool | None,
    sort_name: str,
    role: str | None,
) -> dict[str, Any]:
    """Самый быстрый первый экран.

    Отдаём только 5 последних новостей, 5 последних событий и их общие счётчики.
    График и статистика тем грузятся отдельными фоновыми запросами.
    """
    news_task = list_news(
        q=q,
        topic=topic,
        tag=[],
        region=region,
        product=product,
        source=source,
        period=None,
        date_from=None,
        date_to=None,
        has_photo=has_photo,
        sort_name=sort_name,
        limit=5,
        offset=0,
    )
    featured_task = featured_news(3)
    events_task = list_events(
        q=q,
        topic=topic,
        tag=[],
        region=region,
        product=product,
        source=source,
        period=None,
        date_from=None,
        date_to=None,
        role=role,
        limit=5,
        offset=0,
    )
    news, featured, events = await asyncio.gather(news_task, featured_task, events_task)
    return {
        "news": news,
        "events": events,
        "timeline": None,
        "meta": {
            "total": news.get("total", 0),
            "news_total": news.get("total", 0),
            "events_total": events.get("total", 0),
            "topics": [],
            "regions": [],
            "products": [],
            "tags": [],
            "sources": [],
            "customers": [],
        },
        "featured": featured,
        "top_read": [],
        "mode": "initial",
    }


async def home_payload(
    *,
    q: str | None,
    topic: list[str],
    tag: list[str],
    region: str | None,
    product: str | None,
    source: str | None,
    period: str | None,
    date_from: date | None,
    date_to: date | None,
    has_photo: bool | None,
    sort_name: str,
    role: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Полная главная: новости, события, график, справочники и боковые блоки."""
    events_limit = 100 if date_from and date_to else 5
    news_task = list_news(
        q=q,
        topic=topic,
        tag=tag,
        region=region,
        product=product,
        source=source,
        period=period,
        date_from=date_from,
        date_to=date_to,
        has_photo=has_photo,
        sort_name=sort_name,
        limit=limit,
        offset=offset,
    )
    timeline_task = timeline(days=365, topic=topic, tag=tag, region=region, product=product, source=source)
    events_task = list_events(
        q=q,
        topic=topic,
        tag=tag,
        region=region,
        product=product,
        source=source,
        period=period,
        date_from=date_from,
        date_to=date_to,
        role=role,
        limit=events_limit,
        offset=0,
    )

    news, timeline_data, events, meta, featured, top_read = await asyncio.gather(
        news_task,
        timeline_task,
        events_task,
        news_meta(),
        featured_news(3),
        top_read_news(5),
    )
    return {
        "news": news,
        "timeline": timeline_data,
        "events": events,
        "meta": meta,
        "featured": featured,
        "top_read": top_read,
        "mode": "full",
    }


async def home_background_payload(
    *,
    topic: list[str],
    tag: list[str],
    region: str | None,
    product: str | None,
    source: str | None,
) -> dict[str, Any]:
    """Фоновая подгрузка главной: график, справочники и боковые блоки одним запросом."""
    timeline_data, meta, featured, top_read = await asyncio.gather(
        timeline(days=365, topic=topic, tag=tag, region=region, product=product, source=source),
        news_meta(),
        featured_news(3),
        top_read_news(5),
    )
    return {
        "timeline": timeline_data,
        "meta": meta,
        "featured": featured,
        "top_read": top_read,
    }


async def home_fast_week_payload(
    *,
    q: str | None,
    topic: list[str],
    tag: list[str],
    region: str | None,
    product: str | None,
    source: str | None,
    has_photo: bool | None,
    sort_name: str,
    role: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Быстрый первый экран.

    Возвращает только новости и события за последнюю доступную неделю. Тяжёлые блоки
    (timeline/meta/featured/top-read) фронт подгружает вторым фоновым запросом.
    """
    week_from, week_to = await _latest_week_bounds(
        q=q,
        topic=topic,
        tag=tag,
        region=region,
        product=product,
        source=source,
        has_photo=has_photo,
    )

    news_task = list_news(
        q=q,
        topic=topic,
        tag=tag,
        region=region,
        product=product,
        source=source,
        period=None,
        date_from=week_from,
        date_to=week_to,
        has_photo=has_photo,
        sort_name=sort_name,
        limit=limit,
        offset=offset,
    )
    featured_task = featured_news(3)
    # Для быстрого первого экрана события не ограничиваем неделей.
    # Пока график ещё подгружается, пользователь не выбирал период, поэтому
    # показываем 5 последних событий и сразу отдаём общее количество событий.
    events_task = list_events(
        q=q,
        topic=topic,
        tag=tag,
        region=region,
        product=product,
        source=source,
        period=None,
        date_from=None,
        date_to=None,
        role=role,
        limit=5,
        offset=0,
    )
    news, featured, events = await asyncio.gather(news_task, featured_task, events_task)

    return {
        "news": news,
        "events": events,
        "timeline": None,
        "meta": None,
        "featured": featured,
        "top_read": [],
        "mode": "fast_week",
        "initial_date_from": week_from.isoformat(),
        "initial_date_to": week_to.isoformat(),
    }
