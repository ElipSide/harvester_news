from __future__ import annotations

import asyncio
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from app.schemas.news import EventListResponse, NewsListResponse, NewsMetaResponse, TimelineResponse
from app.services_news import debug_db, featured_news, list_news, news_by_id, news_meta, similar_news, timeline, top_read_news
from app.services_events import event_sources, event_story, events_stats, full_event_graph, list_events, list_events_graph
from app.services_home import home_background_payload, home_fast_week_payload, home_initial_payload, home_payload
from app.services_cache import cache_get, cache_set

_CACHE_SHORT = "public, max-age=60, stale-while-revalidate=120"
_CACHE_META = "public, max-age=120, stale-while-revalidate=300"
_CACHE_TIMELINE = "public, max-age=90, stale-while-revalidate=180"

router = APIRouter(prefix="/news", tags=["news"])


def _db_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=500,
        detail={
            "message": "Ошибка чтения новостей из PostgreSQL",
            "hint": "Проверьте PG_CONNINFO, dbname и схему таблицы news_list. Для нестандартной схемы задайте NEWS_SCHEMA.",
            "error": str(exc),
        },
    )


@router.get("/home/initial")
async def home_initial_endpoint(
    q: str | None = None,
    topic: Annotated[list[str], Query()] = [],
    tag: Annotated[list[str], Query()] = [],
    region: str | None = None,
    product: str | None = None,
    source: str | None = None,
    has_photo: bool | None = None,
    sort: str = Query(default="date_desc", pattern="^(date_desc|date_asc|views_desc|views_asc)$"),
    role: str | None = Query(default=None, pattern="^(farmer|processor|trader|agroholding|exporter)$"),
):
    try:
        return await home_initial_payload(
            q=q,
            topic=topic,
            tag=tag,
            region=region,
            product=product,
            source=source,
            has_photo=has_photo,
            sort_name=sort,
            role=role,
        )
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/home")
async def home_endpoint(
    q: str | None = None,
    topic: Annotated[list[str], Query()] = [],
    tag: Annotated[list[str], Query()] = [],
    region: str | None = None,
    product: str | None = None,
    source: str | None = None,
    period: str | None = Query(default=None, pattern="^(today|week|month|quarter)$"),
    date_from: date | None = None,
    date_to: date | None = None,
    has_photo: bool | None = None,
    sort: str = Query(default="date_desc", pattern="^(date_desc|date_asc|views_desc|views_asc)$"),
    role: str | None = Query(default=None, pattern="^(farmer|processor|trader|agroholding|exporter)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    try:
        return await home_payload(
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
            sort_name=sort,
            role=role,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/home/background")
async def home_background_endpoint(
    response: Response,
    topic: Annotated[list[str], Query()] = [],
    tag: Annotated[list[str], Query()] = [],
    region: str | None = None,
    product: str | None = None,
    source: str | None = None,
):
    response.headers["Cache-Control"] = _CACHE_TIMELINE
    try:
        return await home_background_payload(
            topic=topic,
            tag=tag,
            region=region,
            product=product,
            source=source,
        )
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/home/fast-week")
async def home_fast_week_endpoint(
    q: str | None = None,
    topic: Annotated[list[str], Query()] = [],
    tag: Annotated[list[str], Query()] = [],
    region: str | None = None,
    product: str | None = None,
    source: str | None = None,
    has_photo: bool | None = None,
    sort: str = Query(default="date_desc", pattern="^(date_desc|date_asc|views_desc|views_asc)$"),
    role: str | None = Query(default=None, pattern="^(farmer|processor|trader|agroholding|exporter)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    try:
        return await home_fast_week_payload(
            q=q,
            topic=topic,
            tag=tag,
            region=region,
            product=product,
            source=source,
            has_photo=has_photo,
            sort_name=sort,
            role=role,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("", response_model=NewsListResponse)
async def news_list_endpoint(
    q: str | None = None,
    topic: Annotated[list[str], Query()] = [],
    tag: Annotated[list[str], Query()] = [],
    region: str | None = None,
    product: str | None = None,
    source: str | None = None,
    period: str | None = Query(default=None, pattern="^(today|week|month|quarter)$"),
    date_from: date | None = None,
    date_to: date | None = None,
    has_photo: bool | None = None,
    sort: str = Query(default="date_desc", pattern="^(date_desc|date_asc|views_desc|views_asc)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    include_total: bool = Query(default=True),
):
    try:
        return await list_news(
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
            sort_name=sort,
            limit=limit,
            offset=offset,
            include_total=include_total,
        )
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/events", response_model=EventListResponse)
async def event_list_endpoint(
    q: str | None = None,
    topic: Annotated[list[str], Query()] = [],
    tag: Annotated[list[str], Query()] = [],
    region: str | None = None,
    product: str | None = None,
    source: str | None = None,
    period: str | None = Query(default=None, pattern="^(today|week|month|quarter)$"),
    date_from: date | None = None,
    date_to: date | None = None,
    role: str | None = Query(default=None, pattern="^(farmer|processor|trader|agroholding|exporter)$"),
    limit: int = Query(default=6, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    try:
        return await list_events(
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
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/events/graph")
async def event_graph_endpoint(
    q: str | None = None,
    topic: Annotated[list[str], Query()] = [],
    tag: Annotated[list[str], Query()] = [],
    region: str | None = None,
    product: str | None = None,
    source: str | None = None,
    period: str | None = Query(default=None, pattern="^(today|week|month|quarter)$"),
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=500, ge=1, le=1000),
):
    try:
        return await list_events_graph(
            q=q, topic=topic, tag=tag, region=region, product=product,
            source=source, period=period, date_from=date_from, date_to=date_to,
            limit=limit,
        )
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/events/full_graph")
async def events_full_graph_endpoint(focus_news_id: int | None = Query(default=None)):
    """Полный граф событий + сюжеты + рёбра для explorer на странице чтения новости."""
    try:
        return await full_event_graph(focus_news_id)
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/events/stats")
async def event_stats_endpoint():
    try:
        return await events_stats()
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/events/{event_id}/sources")
async def event_sources_endpoint(event_id: int):
    """Источники одного события — для выпадашки на странице чтения новости."""
    try:
        return {"items": await event_sources(event_id)}
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/featured")
async def featured_news_endpoint(
    response: Response,
    limit: int = Query(default=3, ge=1, le=12),
):
    response.headers["Cache-Control"] = _CACHE_SHORT
    try:
        return await featured_news(limit)
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/top-read")
async def top_read_news_endpoint(
    response: Response,
    limit: int = Query(default=5, ge=1, le=20),
):
    response.headers["Cache-Control"] = _CACHE_SHORT
    try:
        return await top_read_news(limit)
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/meta", response_model=NewsMetaResponse)
async def news_meta_endpoint(response: Response):
    response.headers["Cache-Control"] = _CACHE_META
    try:
        return await news_meta()
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/timeline", response_model=TimelineResponse)
async def timeline_endpoint(
    response: Response,
    days: int = Query(default=365, ge=1, le=365),
    topic: Annotated[list[str], Query()] = [],
    tag: Annotated[list[str], Query()] = [],
    region: str | None = None,
    product: str | None = None,
    source: str | None = None,
):
    response.headers["Cache-Control"] = _CACHE_TIMELINE
    try:
        return await timeline(days=days, topic=topic, tag=tag, region=region, product=product, source=source)
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/debug/db", include_in_schema=False)
async def debug_db_endpoint():
    try:
        return await debug_db()
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/{news_id}/similar")
async def news_similar_endpoint(news_id: int, limit: int = Query(default=3, ge=1, le=6)):
    try:
        items = await similar_news(news_id, limit)
    except Exception as exc:
        raise _db_error(exc) from exc
    return items


@router.get("/{news_id}/card.png", response_class=Response)
async def news_card_image_endpoint(news_id: int):
    cache_key = ("news_card_png", news_id)
    cached = cache_get(cache_key)
    if cached is not None:
        return Response(content=cached, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=600"})
    try:
        item = await news_by_id(news_id)
    except Exception as exc:
        raise _db_error(exc) from exc
    if not item:
        raise HTTPException(status_code=404, detail="Новость не найдена")

    from app.config import settings
    from app.services.image_gen import generate_card_png
    from app.services.card_warmer import _first_topic, _get_photo_for_news

    title = item.get("title") or ""
    topics = item.get("topics")
    tags: list[str] = item.get("tags") or []

    # Фото не обязательно: при отсутствии внешнего фото generate_card_png отдаёт
    # фирменный зелёный градиент. Так фоллбэк-обложка всегда валидна (без 404 в консоли).
    photo_bytes = await _get_photo_for_news(
        title, topics, settings.pexels_api_key, tags=tags, seed=news_id,
    )

    png_bytes = await asyncio.to_thread(
        generate_card_png,
        title=title,
        source=item.get("source"),
        date=item.get("date"),
        topic=_first_topic(topics),
        photo_bytes=photo_bytes,
    )
    cache_set(cache_key, png_bytes, 600)
    return Response(content=png_bytes, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=600"})


@router.get("/{news_id}/story")
async def news_story_endpoint(news_id: int):
    """Сюжетный таймлайн вокруг события, к которому относится новость."""
    try:
        return await event_story(news_id)
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/{news_id}")
async def news_detail_endpoint(news_id: int):
    try:
        item = await news_by_id(news_id)
        if not item:
            raise HTTPException(status_code=404, detail="Новость не найдена")
        return item
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_error(exc) from exc
