from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.db.db_ext import close_pool, open_pool
from psycopg import sql

from app.services.event_tables import ensure_event_schema, write_job_state
from app.services.topic_index import reset_topic_index, sync_topic_index_once, topic_index_stats, rebuild_topic_daily_stats
from app.db.db_ext import get_conn
from app.services_events import process_events_once, prune_stale_inactive_events, rewrite_articles_for_active_events
from app.services.event_graph import rebuild_event_graph

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [events-worker] %(message)s",
)
logger = logging.getLogger(__name__)

_stop = asyncio.Event()


def _request_stop(*_: Any) -> None:
    _stop.set()




async def reset_event_tables() -> None:
    """Очищает готовые события, чтобы пересобрать их новым алгоритмом."""
    await ensure_event_schema()
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                sql.SQL(
                    "TRUNCATE {}.event_links, {}.event_stories, {}.event_impacts, {}.event_sources, {}.events, {}.event_news_state RESTART IDENTITY CASCADE"
                ).format(
                    sql.Identifier(settings.events_schema),
                    sql.Identifier(settings.events_schema),
                    sql.Identifier(settings.events_schema),
                    sql.Identifier(settings.events_schema),
                    sql.Identifier(settings.events_schema),
                    sql.Identifier(settings.events_schema),
                )
            )
        await conn.commit()

async def _run_once() -> dict[str, Any]:
    # Перед сборкой событий дёшево индексируем темы новых новостей.
    # Это питает быстрый график и фильтрацию по темам без JSONB-scan.
    topic_result = await sync_topic_index_once(
        limit=settings.event_worker_fetch_limit,
        lookback_days=settings.event_worker_lookback_days,
        process_all=settings.event_worker_process_all,
    )
    result = await process_events_once()
    combined = {**result, "topics_index": topic_result}
    # Пересобираем сюжетный граф только если события менялись (дёшево, но незачем впустую).
    if int(result.get("events_upserted") or 0) > 0:
        try:
            combined["story_graph"] = await rebuild_event_graph()
        except Exception as exc:
            logger.exception("rebuild_event_graph failed: %s", exc)
            combined["story_graph"] = {"error": str(exc)}
    # Чистим старые неактивные события, чтобы не копить мусор в БД.
    if settings.event_prune_inactive_enabled:
        try:
            combined["pruned"] = await prune_stale_inactive_events()
        except Exception as exc:
            logger.exception("prune_stale_inactive_events failed: %s", exc)
            combined["pruned"] = {"error": str(exc)}
    await write_job_state(
        "events_worker_last_run",
        {
            **combined,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "interval_seconds": settings.event_worker_interval_seconds,
            "analysis_mode": settings.event_analysis_mode,
        },
    )
    return combined


async def worker_loop(once: bool = False, reset: bool = False, drain: bool = False, sync_topics_only: bool = False, reset_topics: bool = False, sync_topic_stats_only: bool = False, rebuild_stories_only: bool = False, prune_only: bool = False, rewrite_articles_only: bool = False, rewrite_all: bool = False, rewrite_limit: int | None = None) -> None:
    await open_pool()
    try:
        await ensure_event_schema()
        if reset:
            logger.warning("reset requested: truncating event tables before processing")
            await reset_event_tables()

        if rewrite_articles_only:
            logger.info("rewriting active events into articles via RAGFlow (all=%s limit=%s)", rewrite_all, rewrite_limit)
            logger.info("rewrite result: %s", await rewrite_articles_for_active_events(limit=rewrite_limit, only_missing=not rewrite_all))
            return

        if prune_only:
            logger.info("pruning stale inactive events: %s", await prune_stale_inactive_events())
            return

        if rebuild_stories_only:
            logger.info("rebuilding event story graph on current events")
            logger.info("story graph result: %s", await rebuild_event_graph())
            return

        if reset_topics:
            logger.warning("reset-topics requested: truncating normalized topic index")
            await reset_topic_index()
        if sync_topic_stats_only:
            logger.info("rebuilding topic daily stats")
            logger.info("topic daily stats result: %s", await rebuild_topic_daily_stats())
            logger.info("topic index stats: %s", await topic_index_stats())
            return

        if sync_topics_only:
            while not _stop.is_set():
                topic_result = await sync_topic_index_once(
                    limit=settings.event_worker_fetch_limit,
                    lookback_days=settings.event_worker_lookback_days,
                    process_all=True if drain else settings.event_worker_process_all,
                )
                logger.info("topic sync result: %s", topic_result)
                if not drain or int(topic_result.get("selected_news") or 0) <= 0:
                    logger.info("topic index stats: %s", await topic_index_stats())
                    break
            return
        logger.info(
            "started: schema=%s interval=%ss batch=%s fetch_limit=%s lookback=%s process_all=%s mode=%s semantic_model=%s semantic_device=%s window=%s cosine=%s strong=%s max_cluster=%s",
            settings.events_schema,
            settings.event_worker_interval_seconds,
            settings.event_worker_batch_size,
            settings.event_worker_fetch_limit,
            settings.event_worker_lookback_days,
            settings.event_worker_process_all,
            settings.event_analysis_mode,
            settings.semantic_embedding_model,
            settings.semantic_device,
            settings.semantic_cluster_window_days,
            settings.semantic_cluster_min_cosine,
            settings.semantic_cluster_strong_cosine,
            settings.semantic_max_cluster_size,
        )

        while not _stop.is_set():
            try:
                result = await _run_once()
                logger.info("run result: %s", result)
            except Exception as exc:
                logger.exception("run failed: %s", exc)
                await write_job_state(
                    "events_worker_last_error",
                    {"error": str(exc), "finished_at": datetime.now(timezone.utc).isoformat()},
                )

            if once:
                # Обычный --once делает один batch.
                # --drain обрабатывает batch-и подряд, пока не останется новых новостей.
                if not drain or int(result.get("fetched") or 0) <= 0:
                    break
                continue

            try:
                await asyncio.wait_for(_stop.wait(), timeout=settings.event_worker_interval_seconds)
            except asyncio.TimeoutError:
                pass
    finally:
        await close_pool()
        logger.info("stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Harvester MVP events from news_list")
    parser.add_argument("--once", action="store_true", help="Process one batch and exit")
    parser.add_argument("--drain", action="store_true", help="With --once: keep processing batches until no unprocessed news are left")
    parser.add_argument("--reset", action="store_true", help="Clear event tables before processing")
    parser.add_argument("--sync-topics", action="store_true", help="Only sync normalized topic index and exit")
    parser.add_argument("--reset-topics", action="store_true", help="Clear normalized topic index before syncing")
    parser.add_argument("--sync-topic-stats", action="store_true", help="Only rebuild pre-aggregated daily topic stats and exit")
    parser.add_argument("--rebuild-stories", action="store_true", help="Only rebuild the event story graph (links + stories) on current events and exit")
    parser.add_argument("--prune", action="store_true", help="Only delete stale inactive (ignored_weak) events older than the configured window and exit")
    parser.add_argument("--rewrite-articles", action="store_true", help="Only rewrite existing active events into RAGFlow articles and exit (requires EVENT_RAGFLOW_ENABLED + RAGFLOW_* env)")
    parser.add_argument("--rewrite-all", action="store_true", help="With --rewrite-articles: rewrite ALL active events, not only those missing an article")
    parser.add_argument("--rewrite-limit", type=int, default=None, help="With --rewrite-articles: cap the number of events to rewrite")
    args = parser.parse_args()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass
    loop.run_until_complete(worker_loop(
        once=args.once,
        reset=args.reset,
        drain=args.drain,
        sync_topics_only=args.sync_topics,
        reset_topics=args.reset_topics,
        sync_topic_stats_only=args.sync_topic_stats,
        rebuild_stories_only=args.rebuild_stories,
        prune_only=args.prune,
        rewrite_articles_only=args.rewrite_articles,
        rewrite_all=args.rewrite_all,
        rewrite_limit=args.rewrite_limit,
    ))


if __name__ == "__main__":
    main()
