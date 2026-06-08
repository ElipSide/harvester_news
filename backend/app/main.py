from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.config import settings
from app.db.db_ext import close_pool, open_pool
from app.db.db_ext_func import fetch_one
from app.routers.news import router as news_router
from app.services.event_tables import ensure_event_schema
from app.services.card_warmer import card_warmer_loop
from app.services_home import home_initial_payload
from app.services_news import news_meta

logger = logging.getLogger(__name__)


async def _prewarm_cache() -> None:
    """Греем кэш первого экрана при старте, чтобы первый пользователь после
    деплоя не платил полную цену запросов к удалённой БД."""
    try:
        from app.services_events import full_event_graph, list_events_graph  # lazy: избегаем цикла импорта
        from app.services_home import home_background_payload
        await asyncio.gather(
            home_initial_payload(
                q=None, topic=[], tag=[], region=None, product=None,
                source=None, has_photo=None, sort_name="date_desc", role=None,
            ),
            news_meta(),
            full_event_graph(None),  # тяжёлый граф страницы чтения (~3с)
            # Фоновый экран главной: timeline(365) (~12с холодных) + meta/featured/top_read.
            home_background_payload(topic=[], tag=[], region=None, product=None, source=None),
            # Граф событий на главной (limit=1000, без фильтров — как зовёт App).
            list_events_graph(topic=[], tag=[], limit=1000),
        )
        logger.info("cache prewarm done")
    except Exception as exc:  # прогрев не должен валить старт
        logger.warning("cache prewarm failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_pool()
    await ensure_event_schema()
    prewarm = asyncio.create_task(_prewarm_cache())
    warmer = asyncio.create_task(card_warmer_loop())
    yield
    prewarm.cancel()
    warmer.cancel()
    await close_pool()


app = FastAPI(title=settings.app_name, version="2.0.0", lifespan=lifespan)

# Сжимаем крупные JSON-ответы (home, events/graph до 1000, timeline 365 дней).
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    row = await fetch_one("SELECT current_database() AS database, current_user AS username")
    return {"status": "ok", "db": row}


app.include_router(news_router, prefix=settings.api_prefix)
