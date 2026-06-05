from __future__ import annotations

import asyncio
import time
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from app.schemas.news import EventListResponse, NewsListResponse, NewsMetaResponse, TimelineResponse
from app.services_news import debug_db, featured_news, list_news, news_by_id, news_meta, similar_news, timeline, top_read_news
from app.services_events import event_detail, event_news_rows, event_story, events_stats, full_event_graph, lab_events, list_events, list_events_graph
from app.services.ragflow_writer import default_prompts, preview_article, source_previews
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
    sort: str | None = Query(default=None, pattern="^(date_desc)$"),
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
            sort=sort,
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


@router.get("/events/lab/list")
async def lab_events_endpoint(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Список активных событий для тест-страницы промтов."""
    try:
        return {"items": await lab_events(q=q, limit=limit)}
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/events/lab/defaults")
async def lab_defaults_endpoint():
    """Текущие дефолтные промты и параметры — для предзаполнения тест-страницы."""
    return default_prompts()


@router.get("/events/lab/sources")
async def lab_sources_endpoint(
    event_id: int = Query(..., ge=1),
    max_source_chars: int | None = Query(default=None, ge=100, le=20000),
):
    """Оригинальные тексты источников события — ровно в том виде, как их видит модель."""
    try:
        rows = await event_news_rows(event_id)
        return {"event_id": event_id, "sources": source_previews(rows, max_source_chars)}
    except Exception as exc:
        raise _db_error(exc) from exc


class _PreviewIn(BaseModel):
    event_id: int
    system_prompt: str | None = None
    user_prompt: str | None = None
    max_source_chars: int | None = None


@router.post("/events/lab/preview")
async def lab_preview_endpoint(payload: _PreviewIn):
    """Синхронная генерация (legacy). Долгая — может упереться в таймаут nginx.
    Фронт использует async-вариант ниже (/preview/start + /preview/result)."""
    try:
        rows = await event_news_rows(payload.event_id)
        if not rows:
            return {"ok": False, "error": "no_sources"}
        return await preview_article(
            rows,
            system_prompt=payload.system_prompt,
            user_prompt=payload.user_prompt,
            max_source_chars=payload.max_source_chars,
        )
    except Exception as exc:
        raise _db_error(exc) from exc


# --- Async-генерация лаборатории: kick-off + polling -------------------------
# RAGFlow генерит статью 50–360с. Держать одно HTTP-соединение так долго нельзя —
# любой nginx по пути рвёт его по proxy_read_timeout (504). Поэтому POST /start
# мгновенно отдаёт job_id и запускает генерацию в фоне, а фронт коротко поллит
# GET /result. In-memory стор безопасен: backend — один uvicorn-процесс.
_LAB_JOBS: dict[str, dict] = {}
_LAB_TASKS: set[asyncio.Task] = set()
_LAB_JOB_TTL = 1800.0  # сек: сколько храним завершённую задачу
_LAB_JOBS_MAX = 200


def _lab_jobs_prune() -> None:
    now = time.monotonic()
    stale = [
        jid for jid, j in _LAB_JOBS.items()
        if j.get("status") in ("done", "error") and (now - j.get("ts", now)) > _LAB_JOB_TTL
    ]
    for jid in stale:
        _LAB_JOBS.pop(jid, None)
    # Жёсткий предохранитель от утечки памяти.
    if len(_LAB_JOBS) > _LAB_JOBS_MAX:
        for jid in sorted(_LAB_JOBS, key=lambda k: _LAB_JOBS[k].get("ts", 0.0))[: len(_LAB_JOBS) - _LAB_JOBS_MAX]:
            _LAB_JOBS.pop(jid, None)


async def _lab_run_job(job_id: str, rows, system_prompt, user_prompt, max_source_chars) -> None:
    try:
        res = await preview_article(
            rows,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_source_chars=max_source_chars,
        )
        _LAB_JOBS[job_id] = {"status": "done", "result": res, "ts": time.monotonic()}
    except Exception as exc:  # noqa: BLE001 — фон-задача не должна падать молча
        _LAB_JOBS[job_id] = {
            "status": "error",
            "result": {"ok": False, "error": str(exc)},
            "ts": time.monotonic(),
        }


@router.post("/events/lab/preview/start")
async def lab_preview_start_endpoint(payload: _PreviewIn):
    """Запускает генерацию в фоне, мгновенно возвращает job_id."""
    try:
        rows = await event_news_rows(payload.event_id)
    except Exception as exc:
        raise _db_error(exc) from exc
    if not rows:
        return {"ok": False, "error": "no_sources"}

    _lab_jobs_prune()
    job_id = uuid.uuid4().hex
    _LAB_JOBS[job_id] = {"status": "running", "result": None, "ts": time.monotonic()}
    task = asyncio.create_task(
        _lab_run_job(job_id, rows, payload.system_prompt, payload.user_prompt, payload.max_source_chars)
    )
    _LAB_TASKS.add(task)
    task.add_done_callback(_LAB_TASKS.discard)
    return {"ok": True, "job_id": job_id, "status": "running"}


@router.get("/events/lab/preview/result")
async def lab_preview_result_endpoint(job_id: str = Query(..., min_length=8, max_length=64)):
    """Опрос статуса фоновой генерации."""
    job = _LAB_JOBS.get(job_id)
    if job is None:
        return {"status": "unknown"}
    if job["status"] == "running":
        return {"status": "running"}
    return {"status": job["status"], "result": job["result"]}


@router.get("/events/{event_id}/detail")
async def event_detail_endpoint(event_id: int):
    """Источники + impacts по ролям события — для шапки страницы чтения новости."""
    try:
        return await event_detail(event_id)
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
