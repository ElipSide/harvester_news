from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _read_dotenv() -> None:
    """Минимальная загрузка .env без внешних зависимостей.

    Docker Compose обычно сам прокидывает env, но при локальном запуске
    `uvicorn app.main:app --reload` удобно, чтобы backend сам прочитал .env.
    """
    root = Path(__file__).resolve().parents[2]
    env_path = root.parent / ".env"
    if not env_path.exists():
        env_path = root / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_read_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Harvester News API")
    api_prefix: str = os.getenv("API_PREFIX", "/api/v1")

    # Строка подключения к PostgreSQL. Реальное значение задаётся через переменную
    # окружения PG_CONNINFO (см. .env / .env.example). Дефолт — только плейсхолдер.
    # Формат: dbname=DBNAME user=USER password=PASSWORD host=DB_HOST port=5432
    pg_conninfo: str = os.getenv(
        "PG_CONNINFO",
        "dbname=DBNAME user=USER password=PASSWORD host=DB_HOST port=5432",
    )

    # Если NEWS_SCHEMA пустой — backend сам ищет схему, где лежит news_list.
    news_schema: str | None = os.getenv("NEWS_SCHEMA") or None
    news_table: str = os.getenv("NEWS_TABLE", "news_list")

    cors_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000",
        ).split(",")
        if origin.strip()
    )

    # min_size=4: БД удалённая, холодный коннект (TCP+TLS+auth) дорог.
    # Держим тёплый пул, чтобы первый запрос после простоя не платил за установку соединения.
    db_pool_min_size: int = int(os.getenv("DB_POOL_MIN_SIZE", "4"))
    db_pool_max_size: int = int(os.getenv("DB_POOL_MAX_SIZE", "10"))

    # Постоянный слой событий. Таблицы создаются отдельно от news_list,
    # чтобы не конфликтовать с уже существующей схемой.
    events_schema: str = os.getenv("EVENTS_SCHEMA", "harvester_news")

    # Анализ событий без платных LLM.
    # Worker использует локальную RAG-like схему: фильтр качества → retrieval/кластеризация
    # похожих публикаций → extractive summary → rule-based impact по ролям.
    # Режимы:
    # - offline-rag: старый бесплатный токен/тег/date clustering;
    # - semantic-rag: более сильная локальная RAG-схема через open-source multilingual embeddings.
    event_analysis_mode: str = os.getenv("EVENT_ANALYSIS_MODE", "semantic-rag")
    event_cluster_window_days: int = int(os.getenv("EVENT_CLUSTER_WINDOW_DAYS", "5"))
    event_cluster_min_similarity: float = float(os.getenv("EVENT_CLUSTER_MIN_SIMILARITY", "0.24"))
    event_context_sources_limit: int = int(os.getenv("EVENT_CONTEXT_SOURCES_LIMIT", "12"))
    # Минимальное число уникальных источников, при котором событие считается пригодным
    # для показа в интерфейсе. Слабые события сохраняются как ignored_weak, чтобы
    # worker не обрабатывал те же новости снова, но API их не отдаёт.
    event_min_sources: int = int(os.getenv("EVENT_MIN_SOURCES", "3"))

    # Настройки semantic-rag. Платные LLM не используются: модель embeddings локальная.
    semantic_rag_enabled: bool = os.getenv("SEMANTIC_RAG_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    semantic_embedding_model: str = os.getenv("SEMANTIC_EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    semantic_device: str = os.getenv("SEMANTIC_DEVICE", "cpu")
    semantic_batch_size: int = int(os.getenv("SEMANTIC_BATCH_SIZE", "32"))
    semantic_cluster_window_days: int = int(os.getenv("SEMANTIC_CLUSTER_WINDOW_DAYS", "5"))
    semantic_cluster_min_cosine: float = float(os.getenv("SEMANTIC_CLUSTER_MIN_COSINE", "0.66"))
    semantic_cluster_strong_cosine: float = float(os.getenv("SEMANTIC_CLUSTER_STRONG_COSINE", "0.82"))
    semantic_min_token_overlap: float = float(os.getenv("SEMANTIC_MIN_TOKEN_OVERLAP", "0.12"))
    # Дополнительные предохранители от "сваливания" разных инфоповодов в один кластер.
    semantic_min_title_overlap: float = float(os.getenv("SEMANTIC_MIN_TITLE_OVERLAP", "0.18"))
    semantic_min_cluster_cohesion: float = float(os.getenv("SEMANTIC_MIN_CLUSTER_COHESION", "0.64"))
    semantic_exclude_digest_sources: bool = os.getenv("SEMANTIC_EXCLUDE_DIGEST_SOURCES", "true").lower() in {"1", "true", "yes", "on"}
    semantic_max_cluster_size: int = int(os.getenv("SEMANTIC_MAX_CLUSTER_SIZE", "6"))

    event_worker_interval_seconds: int = int(os.getenv("EVENT_WORKER_INTERVAL_SECONDS", "300"))
    event_worker_batch_size: int = int(os.getenv("EVENT_WORKER_BATCH_SIZE", "300"))
    event_worker_fetch_limit: int = int(os.getenv("EVENT_WORKER_FETCH_LIMIT", "1000"))
    event_worker_lookback_days: int = int(os.getenv("EVENT_WORKER_LOOKBACK_DAYS", "365"))
    event_worker_process_all: bool = os.getenv("EVENT_WORKER_PROCESS_ALL", "false").lower() in {"1", "true", "yes", "on"}

    # Очистка БД: удаляем неактивные (ignored_weak) события старше N дней относительно
    # самой свежей даты события. Они уже не доберут источников (окно склейки 5 дней),
    # поэтому копить их в БД незачем. Новости остаются помеченными в event_news_state.
    event_prune_inactive_enabled: bool = os.getenv("EVENT_PRUNE_INACTIVE_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    event_prune_inactive_days: int = int(os.getenv("EVENT_PRUNE_INACTIVE_DAYS", "5"))

    # Pixabay API для фоновых изображений карточек новостей.
    # Получить бесплатно: https://pixabay.com/api/
    # Если пустой — карточки генерируются с зелёным градиентом (fallback).
    pixabay_api_key: str = os.getenv("PIXABAY_API_KEY", "")

    # Pexels API для фоновых изображений карточек новостей.
    # Получить бесплатно: https://www.pexels.com/api/
    # Приоритет выше Pixabay — не блокирует VPS. 20 000 запросов/месяц бесплатно.
    pexels_api_key: str = os.getenv("PEXELS_API_KEY", "")

    # Основной пользовательский слой: темы берутся напрямую из колонки news_list.topics.

    # Mixed Content fix: некоторые link_photo приходят как http://<host>/<file>.jpg.
    # Страница открывается по HTTPS, поэтому браузер блокирует такие картинки.
    # Мы переписываем эти URL на свой же origin (same-origin, HTTPS), а nginx
    # фронтенда проксирует их на исходный хост.
    # INSECURE_IMAGE_HOST — host[:port] без схемы (можно перечислить через запятую).
    insecure_image_hosts: tuple[str, ...] = tuple(
        h.strip()
        for h in os.getenv("INSECURE_IMAGE_HOST", "77.238.253.92:8765").split(",")
        if h.strip()
    )
    # Префикс, под которым nginx фронтенда проксирует картинки (см. frontend/nginx.conf).
    image_proxy_prefix: str = os.getenv("IMAGE_PROXY_PREFIX", "/test_news/imgproxy")


settings = Settings()
