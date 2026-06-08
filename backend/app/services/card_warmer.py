from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from typing import Any

from app.services_cache import cache_get, cache_set
from app.services.image_gen import generate_card_png

logger = logging.getLogger(__name__)

_WARM_INTERVAL = 120   # совпадает с TTL featured_news
_CARD_TTL = 600        # 10 мин — TTL PNG-карточки
_PHOTO_TTL = 3600      # 1 час — TTL сырых байт фото

# ─── Ключевые слова в заголовке → поисковый запрос Pexels ────────────────────
# Порядок важен: более специфичные термины — раньше.
_TITLE_KEYWORDS: list[tuple[str, str]] = [
    # Конкретные культуры
    ("пшениц",          "wheat field harvest golden sun"),
    ("ячмен",           "barley grain field ears"),
    ("кукуруз",         "corn maize field harvest"),
    ("подсолнечник",    "sunflower field yellow harvest"),
    ("подсолнух",       "sunflower field yellow"),
    ("соев",            "soybean field harvest green"),
    (" соя ",           "soybean field green harvest"),
    ("рапс",            "rapeseed canola yellow field bloom"),
    ("гречих",          "buckwheat white flowers field"),
    ("овса",            "oats grain cereal field"),
    ("овёс",            "oats grain cereal field"),
    (" овес ",          "oats grain cereal field"),
    ("просо",           "millet grain field harvest"),
    ("рожь",            "rye grain field ears"),
    ("тритикал",        "triticale grain field"),
    ("горох",           "peas green field legume"),
    ("нута",            "chickpea legume harvest"),
    (" нут ",           "chickpea legume harvest"),
    ("чечевиц",         "lentil legume field harvest"),
    ("кориандр",        "coriander herb spice field"),
    ("льн",             "flax blue flower field"),
    (" лён",            "flax blue flower field"),
    ("амарант",         "amaranth plant grain harvest"),
    ("рис",             "rice paddy field water"),
    ("гриб",            "mushroom farm harvest"),
    ("орех",            "nuts walnut harvest tree"),
    # Животноводство
    ("молок",           "dairy cow farm milk"),
    ("свинин",          "pig farm livestock"),
    ("птиц",            "poultry chicken farm"),
    ("говядин",         "beef cattle livestock farm"),
    ("мяс",             "livestock cattle farm"),
    # Стихии / погода
    ("засух",           "drought dry cracked earth field"),
    ("заморозк",        "frost ice agriculture field morning"),
    ("наводнен",        "flood water field agriculture"),
    ("град",            "hail storm wheat field damage"),
    ("ливен",           "rain storm field agriculture"),
    # Агрооперации
    ("посев",           "sowing planting seeds tractor field"),
    ("сева ",           "sowing planting seeds tractor field"),
    ("жатв",            "combine harvester wheat field"),
    ("уборк",           "combine harvester grain field"),
    ("жнив",            "combine harvester field golden"),
    ("вспашк",          "tractor plowing field soil"),
    ("орошен",          "irrigation water field sprinkler"),
    ("полив",           "irrigation water field"),
    # Логистика / хранение
    ("элеватор",        "grain elevator silo storage"),
    ("силос",           "grain silo storage agriculture"),
    ("хранен",          "grain silo warehouse storage"),
    ("перевозк",        "grain truck transport logistics"),
    ("логистик",        "truck transport grain logistics"),
    # Торговля / рынок
    ("экспорт",         "grain cargo ship port export"),
    ("импорт",          "cargo ship port import"),
    ("пошлин",          "customs border cargo ship"),
    ("квот",            "grain trade policy document"),
    ("цен",             "grain market price commodity"),
    ("биржа",           "commodity exchange market trading"),
    # Инфраструктура
    ("порт",            "grain port terminal ship"),
    ("терминал",        "grain terminal port ship"),
    ("завод",           "factory plant industrial"),
    ("переработк",      "flour mill grain processing"),
    ("мельниц",         "flour mill grain wheat"),
    # Агрохимия
    ("удобрен",         "fertilizer agriculture green field"),
    ("пестицид",        "pesticide spray crop field"),
    ("гербицид",        "herbicide spray field tractor"),
    ("средств защит",   "pesticide spray field crop"),
    # Прочее агро
    ("семен",           "seeds agriculture plant seedling"),
    ("техник",          "agricultural machinery tractor field"),
    ("трактор",         "tractor field agriculture soil"),
    ("агрохолдинг",     "large agribusiness farm aerial"),
    ("субсиди",         "agriculture support green field"),
    ("страховани",      "agriculture field harvest sky"),
]

# ─── Маппинг тем → поисковый запрос (резерв, если заголовок не дал совпадений) ─
_TOPIC_QUERIES: dict[str, str] = {
    "Зерновые":               "grain wheat cereal field",
    "Пшеница":                "wheat field harvest golden",
    "Урожай":                 "crop harvest agriculture combine",
    "Масличные":              "sunflower rapeseed oilseed field",
    "Подсолнечник":           "sunflower field harvest yellow",
    "Соя":                    "soybean field harvest agriculture",
    "Рапс":                   "rapeseed canola yellow field",
    "Кукуруза":               "corn maize field harvest",
    "Рис":                    "rice paddy field water",
    "Гречиха":                "buckwheat field flowers",
    "Ячмень":                 "barley field grain ears",
    "Рожь":                   "rye field grain agriculture",
    "Овес":                   "oats field grain cereal",
    "Просо":                  "millet field grain harvest",
    "Горох":                  "peas field legume green",
    "Нут":                    "chickpea legume field harvest",
    "Чечевица":               "lentil legume field agriculture",
    "Зернобобовые":           "legume beans field harvest",
    "Крупяные":               "grain cereal crop field",
    "Крупы":                  "grain cereal food bowl",
    "Овощи":                  "vegetables farm field harvest",
    "Фрукты":                 "fruit orchard farm apple",
    "Ягоды":                  "berries strawberry farm field",
    "Бахчевые":               "watermelon melon field harvest",
    "Масло":                  "sunflower oil bottle golden",
    "Логистика":              "grain truck transport logistics",
    "Экспорт":                "cargo ship grain port export",
    "Импорт":                 "cargo ship port logistics",
    "Хранение":               "grain silo storage elevator",
    "Переработка":            "flour mill grain processing plant",
    "Технологии":             "agriculture technology precision drone",
    "Агрохолдинги":           "agribusiness large farm aerial",
    "Регулирование":          "agriculture policy government document",
    "Торговля":               "grain trade market exchange",
    "Аналитика":              "agriculture data analytics field",
    "Мероприятия":            "agriculture exhibition fair event",
    "Семена":                 "seeds agriculture plant seedling",
    "Сев":                    "sowing planting tractor field",
    "Уборка":                 "combine harvester wheat field",
    "Порт":                   "grain port terminal ship",
    "Терминал":               "grain elevator terminal port",
    "Таможня":                "customs border cargo",
    "Суд":                    "courthouse law justice building",
    "Иск":                    "courthouse legal justice law",
    "Деятели":                "business people agriculture meeting",
    "Завод":                  "factory plant industrial building",
    "Производители удобрений": "fertilizer plant chemical agriculture",
    "Производители СЗР":      "pesticide spray agriculture field",
    "Пищевые компании":       "food factory processing plant",
    "Трейдеры":               "commodities trading market grain",
    "Амарант":                "amaranth plant grain harvest",
    "Лен":                    "flax blue flower field",
    "Грибы":                  "mushroom farm harvest grow",
    "Лекарственные":          "medicinal herbs plants field",
    "Специи":                 "spices herbs agriculture market",
    "Чай":                    "tea plantation harvest green",
    "Кофе":                   "coffee plantation harvest beans",
    "Орехи":                  "nuts harvest walnut tree",
    "Россия":                 "russia agriculture wheat field",
    "Азия":                   "asia rice paddy field",
    "Европа":                 "europe agriculture farm field",
    "Африка":                 "africa agriculture farm harvest",
    "Северная Америка":       "north america wheat farm",
    "Южная Америка":          "south america soybean corn farm",
    "Океания":                "australia wheat farm field",
    "ЦФО":                    "russia central agriculture field",
    "ЮФО":                    "south russia wheat steppe field",
    "ПФО":                    "volga russia agriculture wheat",
    "СКФО":                   "caucasus russia agriculture",
    "СФО":                    "siberia russia agriculture field",
    "ДФО":                    "russia far east agriculture",
    "УФО":                    "urals russia agriculture field",
    "СЗФО":                   "russia northwest agriculture field",
    "Проблемы":               "drought flood agriculture problem",
    "Линия":                  "agricultural conveyor processing line",
}
_DEFAULT_QUERY = "agriculture farm field harvest golden"


def _build_query(title: str, topics: Any, tags: list[str] | None = None) -> str:
    """Строит поисковый запрос Pexels по заголовку, тегам и темам новости.

    Приоритет: ключевые слова в заголовке > теги > темы из БД > дефолт.
    """
    title_lower = (title or "").lower()

    # 1. Сканируем заголовок — ищем первое совпадение по стемам
    for keyword, query in _TITLE_KEYWORDS:
        if keyword in title_lower:
            return query

    # 2. Сканируем теги (уже включают topics/regions/products из БД)
    for tag in (tags or []):
        # Точное совпадение с картой тем
        q = _TOPIC_QUERIES.get(tag)
        if q:
            return q
        # Совпадение по стему (например, тег "пшеница озимая" → "пшениц")
        tag_lower = tag.lower()
        for keyword, query in _TITLE_KEYWORDS:
            if keyword in tag_lower:
                return query

    # 3. Резерв: topics напрямую
    if isinstance(topics, dict):
        for topic in topics:
            q = _TOPIC_QUERIES.get(topic)
            if q:
                return q
    elif isinstance(topics, list):
        for topic in topics:
            q = _TOPIC_QUERIES.get(topic)
            if q:
                return q

    return _DEFAULT_QUERY


def _download_url_sync(photo_url: str) -> bytes | None:
    """Скачивает байты по URL."""
    try:
        req = urllib.request.Request(photo_url, headers={"User-Agent": "HarvesterNews/2.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
    except Exception as exc:
        logger.warning("photo download failed (%s): %s", photo_url, exc)
        return None


def _fetch_pexels_urls_sync(query: str, api_key: str, count: int = 5) -> list[str]:
    """Возвращает список URL фото из Pexels (до count штук).

    Не блокирует VPS. 20 000 запросов/месяц бесплатно.
    Список URL кэшируется; конкретное фото выбирается по news_id для разнообразия.
    """
    encoded = urllib.parse.quote(query)
    url = f"https://api.pexels.com/v1/search?query={encoded}&per_page={count}&orientation=landscape"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "HarvesterNews/2.0", "Authorization": api_key},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        logger.warning("pexels search failed for %r: %s", query, exc)
        return []

    urls: list[str] = []
    for photo in data.get("photos") or []:
        src = photo.get("src") or {}
        photo_url = src.get("large2x") or src.get("large") or src.get("medium")
        if photo_url:
            urls.append(photo_url)
    return urls


async def _get_photo_for_news(
    title: str,
    topics: Any,
    pexels_api_key: str,
    tags: list[str] | None = None,
    seed: int = 0,
) -> bytes | None:
    """Возвращает байты фото для новости через Pexels.

    Запрос строится по заголовку, тегам и темам.
    seed (news_id) используется для выбора разных фото из одного набора результатов.
    Если Pexels не задан или не ответил — возвращает None (показываем «Нет фото»).

    Кэш URL-списка — 1 час. Кэш байт — 1 час (по URL).
    """
    if not pexels_api_key:
        return None

    query = _build_query(title, topics, tags)

    # 1. Получаем список URL (1 запрос к Pexels на всю тему, кэш 1 час)
    urls_key = ("pexels_urls", query)
    urls: list[str] | None = cache_get(urls_key)
    if urls is None:
        urls = await asyncio.to_thread(_fetch_pexels_urls_sync, query, pexels_api_key)
        if urls:
            cache_set(urls_key, urls, _PHOTO_TTL)
            logger.debug("pexels urls: query=%r got %d results", query, len(urls))
        else:
            logger.debug("pexels: no results for query=%r title=%r", query, (title or "")[:60])

    if not urls:
        return None

    # 2. Выбираем URL по seed — разные новости с одной темой получают разные фото
    photo_url = urls[seed % len(urls)]

    # 3. Скачиваем байты (кэш по URL, 1 час)
    bytes_key = ("pexels_bytes", photo_url)
    photo: bytes | None = cache_get(bytes_key)
    if photo is None:
        photo = await asyncio.to_thread(_download_url_sync, photo_url)
        if photo:
            cache_set(bytes_key, photo, _PHOTO_TTL)
            logger.debug("pexels photo: query=%r seed=%d url_idx=%d %d bytes",
                         query, seed, seed % len(urls), len(photo))
    return photo


async def warm_featured_cards() -> None:
    """Прегенерирует PNG-карточки для топ-3 featured новостей."""
    try:
        from app.config import settings
        from app.services_news import featured_news

        pexels_key: str = getattr(settings, "pexels_api_key", "") or ""

        items = await featured_news(3)
        for item in items:
            news_id = item.get("id")
            if not news_id:
                continue

            cache_key = ("news_card_png", news_id)
            if cache_get(cache_key) is not None:
                continue

            title = item.get("title") or ""
            topics = item.get("topics")
            tags: list[str] = item.get("tags") or []

            photo_bytes = await _get_photo_for_news(
                title, topics, pexels_key, tags=tags, seed=news_id,
            )
            if not photo_bytes:
                # Нет фото — карточку не генерируем; endpoint вернёт 404
                logger.debug("card warmer: no photo for news_id=%s, skipping", news_id)
                continue

            png = await asyncio.to_thread(
                generate_card_png,
                title=title,
                source=item.get("source"),
                date=item.get("date"),
                topic=_first_topic(topics),
                photo_bytes=photo_bytes,
            )
            cache_set(cache_key, png, _CARD_TTL)
            logger.debug("card warmed: news_id=%s query=%r", news_id,
                         _build_query(title, topics, tags))
    except Exception:
        logger.exception("card warmer error (non-fatal)")


def _first_topic(topics: Any) -> str | None:
    """Извлекает первую тему из dict или list."""
    if not topics:
        return None
    if isinstance(topics, dict):
        return next(iter(topics), None)
    if isinstance(topics, list):
        return topics[0] if topics else None
    return None


async def _warm_heavy_caches() -> None:
    """Держит тёплыми тяжёлые кэши, чтобы холодную сборку платил фон, а не пользователь:
    граф страницы чтения (~3с), фоновый экран главной timeline(365) (~12с) и граф главной."""
    try:
        from app.services_events import full_event_graph, list_events_graph  # lazy: цикл импорта
        from app.services_home import home_background_payload
        await asyncio.gather(
            full_event_graph(None),
            home_background_payload(topic=[], tag=[], region=None, product=None, source=None),
            list_events_graph(topic=[], tag=[], limit=1000),
        )
    except Exception:
        logger.debug("heavy cache warm failed (non-fatal)", exc_info=True)


async def card_warmer_loop() -> None:
    await asyncio.sleep(15)  # дать пулу БД подняться
    while True:
        await warm_featured_cards()
        await _warm_heavy_caches()
        await asyncio.sleep(_WARM_INTERVAL)
