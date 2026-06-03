from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
import hashlib
import re
from typing import Any

from psycopg import sql

from app.config import settings
from app.db.db_ext import news_table_identifier, resolve_news_schema
from app.db.db_ext_func import fetch_all, fetch_one, fetch_val
from app.utils.news_utils import extract_tags, facet_add, facet_add_list, facet_list, json_to_list, period_bounds, row_to_news
from app.services_cache import cache_get, cache_set, frozen_list

NEWS_COLUMNS = sql.SQL(
    """
    n.id,
    n.id_message,
    n.date,
    n.text,
    n.tag,
    n.link_site,
    n.source,
    n.link_photo,
    n.customer,
    n.object,
    n.extra_tag,
    n.title,
    COALESCE(n.views, 0) AS views,
    COALESCE(n.subscribers, 0) AS subscribers,
    n.regions,
    n.products,
    n.topics
    """
)

# Облегчённая выборка для ленты: без полного text и тяжёлых JSONB-полей.
NEWS_LIST_COLUMNS = sql.SQL(
    """
    n.id,
    n.id_message,
    n.date,
    LEFT(
        BTRIM(regexp_replace(COALESCE(n.text, ''), '[[:space:]]+', ' ', 'g')),
        320
    ) AS text_snippet,
    n.link_site,
    n.source,
    n.link_photo,
    n.customer,
    n.title,
    COALESCE(n.views, 0) AS views,
    COALESCE(n.subscribers, 0) AS subscribers,
    n.regions,
    n.products,
    n.topics
    """
)

# Глобальный фильтр качества: не показываем технические/пустые записи,
# link-only посты, заглушки без заголовка и записи без осмысленного текста.
# Используется во всех пользовательских выборках: лента, события, топ, featured, meta, timeline.
QUALITY_WHERE = sql.SQL(
    """
    NULLIF(BTRIM(COALESCE(n.title, '')), '') IS NOT NULL
    AND LOWER(BTRIM(COALESCE(n.title, ''))) NOT IN (
        'без заголовка', 'без названия', 'нет заголовка', 'none', 'null', '-'
    )
    AND NOT (BTRIM(COALESCE(n.title, '')) ~* '^(https?://|www[.]|t[.]me/|@)')
    AND NULLIF(BTRIM(COALESCE(n.text, '')), '') IS NOT NULL
    AND CHAR_LENGTH(BTRIM(regexp_replace(COALESCE(n.text, ''), '[[:space:]]+', ' ', 'g'))) >= 50
    AND CHAR_LENGTH(regexp_replace(COALESCE(n.text, ''), '[^A-Za-zА-Яа-яЁё]+', '', 'g')) >= 30
    AND NOT (
        BTRIM(COALESCE(n.text, '')) ~* '^(https?://|www[.]|t[.]me/|@)[^A-Za-zА-Яа-яЁё]{0,80}$'
    )
    AND NOT (
        (
            LOWER(COALESCE(n.text, '')) LIKE '%%telegram | max%%'
            OR LOWER(COALESCE(n.text, '')) LIKE '%%дашборд ksm%%'
        )
        AND CHAR_LENGTH(regexp_replace(COALESCE(n.text, ''), '[^A-Za-zА-Яа-яЁё]+', '', 'g')) < 70
    )
    """
)




def _topic_marks_table() -> sql.Composed:
    return sql.Identifier(settings.events_schema, "news_topic_marks")


def _topic_daily_stats_table() -> sql.Composed:
    return sql.Identifier(settings.events_schema, "topic_daily_stats")


def _topic_daily_totals_table() -> sql.Composed:
    return sql.Identifier(settings.events_schema, "topic_daily_totals")


def _topic_norm(value: str) -> str:
    return (value or "").strip().casefold().replace("ё", "е")

def _json_text_filter(field: str, param_name: str) -> sql.Composed:
    return sql.SQL("LOWER(COALESCE(n.{field}::text, '')) LIKE ANY(%({param})s)").format(
        field=sql.Identifier(field),
        param=sql.SQL(param_name),
    )


def _json_has_all_filter(field: str, param_name: str) -> sql.Composed:
    return sql.SQL("(CASE WHEN jsonb_typeof(n.{field}) IN ('array','object') THEN n.{field} ELSE '[]'::jsonb END) ?& %({param})s").format(
        field=sql.Identifier(field),
        param=sql.SQL(param_name),
    )


def _json_has_one_filter(field: str, param_name: str) -> sql.Composed:
    return sql.SQL("(CASE WHEN jsonb_typeof(n.{field}) IN ('array','object') THEN n.{field} ELSE '[]'::jsonb END) ? %({param})s").format(
        field=sql.Identifier(field),
        param=sql.SQL(param_name),
    )


def _tag_search_expr() -> sql.SQL:
    return sql.SQL(
        "LOWER(CONCAT_WS(' ', "
        "COALESCE(n.topics::text, ''), "
        "COALESCE(n.regions::text, ''), "
        "COALESCE(n.products::text, ''), "
        "COALESCE(n.tag::text, ''), "
        "COALESCE(n.extra_tag::text, ''), "
        "COALESCE(n.object::text, '')"
        "))"
    )


def _build_filters(
    *,
    q: str | None = None,
    topics: list[str] | None = None,
    tags: list[str] | None = None,
    region: str | None = None,
    product: str | None = None,
    source: str | None = None,
    period: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    has_photo: bool | None = None,
) -> tuple[sql.Composed, dict[str, Any]]:
    where: list[sql.Composable] = [QUALITY_WHERE]
    params: dict[str, Any] = {}

    if q:
        params["q"] = q
        params["q_like"] = f"%{q.lower()}%"
        where.append(
            sql.SQL(
                "(" 
                "LOWER(COALESCE(n.title, '')) LIKE %(q_like)s OR "
                "LOWER(COALESCE(n.text, '')) LIKE %(q_like)s OR "
                "LOWER(COALESCE(n.source, '')) LIKE %(q_like)s OR "
                "LOWER(COALESCE(n.customer, '')) LIKE %(q_like)s"
                ")"
            )
        )

    # Основной слой фильтрации — темы. Старый query-параметр tag
    # оставляем только для обратной совместимости и трактуем как topic.
    # Важно: больше не ищем по tag/extra_tag/object, чтобы не запускать
    # тяжёлый JSONB-scan по сырой ленте.
    effective_topics: list[str] = []
    for value in [*(topics or []), *(tags or [])]:
        if value and value not in effective_topics:
            effective_topics.append(value)

    if effective_topics:
        norm_topics = [_topic_norm(v) for v in effective_topics if _topic_norm(v)]
        if norm_topics:
            params["topic_norm_values"] = norm_topics
            params["topic_norm_count"] = len(set(norm_topics))
            where.append(
                sql.SQL(
                    """
                    n.id IN (
                        SELECT nt.news_id
                        FROM {marks} nt
                        WHERE nt.topic_norm = ANY(%(topic_norm_values)s)
                        GROUP BY nt.news_id
                        HAVING COUNT(DISTINCT nt.topic_norm) = %(topic_norm_count)s
                    )
                    """
                ).format(marks=_topic_marks_table())
            )

    if region:
        params["region_value"] = region
        where.append(_json_has_one_filter("regions", "region_value"))

    if product:
        params["product_value"] = product
        where.append(_json_has_one_filter("products", "product_value"))

    if source:
        params["source"] = source.lower()
        where.append(sql.SQL("LOWER(COALESCE(n.source, '')) = %(source)s"))

    # Если пользователь кликнул по графику, фронт передаёт date_from/date_to.
    # date_to трактуем как включительно, поэтому в SQL добавляем +1 день и используем <.
    if date_from and date_to:
        if date_to < date_from:
            date_from, date_to = date_to, date_from
        params["date_from"] = datetime.combine(date_from, time.min)
        params["date_to"] = datetime.combine(date_to + timedelta(days=1), time.min)
        where.append(sql.SQL("n.date >= %(date_from)s AND n.date < %(date_to)s"))
    else:
        period_from, period_to = period_bounds(period)
        if period_from and period_to:
            params["date_from"] = period_from
            params["date_to"] = period_to
            where.append(sql.SQL("n.date >= %(date_from)s AND n.date < %(date_to)s"))

    if has_photo is True:
        where.append(sql.SQL("n.link_photo IS NOT NULL AND n.link_photo <> ''"))
    elif has_photo is False:
        where.append(sql.SQL("(n.link_photo IS NULL OR n.link_photo = '')"))

    clause = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where) if where else sql.SQL("")
    return clause, params


def _order_by(sort_name: str | None) -> sql.SQL:
    mapping = {
        "date_asc": "n.date ASC NULLS LAST, n.id ASC",
        "views_desc": "COALESCE(n.views, 0) DESC, n.date DESC NULLS LAST",
        "views_asc": "COALESCE(n.views, 0) ASC, n.date DESC NULLS LAST",
        "date_desc": "n.date DESC NULLS LAST, n.id DESC",
    }
    return sql.SQL(mapping.get(sort_name or "date_desc", mapping["date_desc"]))


async def list_news(
    *,
    q: str | None,
    topic: list[str],
    tag: list[str],
    region: str | None,
    product: str | None,
    source: str | None,
    period: str | None,
    date_from: date | None = None,
    date_to: date | None = None,
    has_photo: bool | None = None,
    sort_name: str,
    limit: int,
    offset: int,
    include_total: bool = True,
) -> dict[str, Any]:
    cache_key = (
        "list_news_topic_marks_v2", q or "", frozen_list(topic), frozen_list(tag), region or "", product or "",
        source or "", period or "", str(date_from or ""), str(date_to or ""), has_photo, sort_name, limit, offset,
        include_total,
    )
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    table = await news_table_identifier()
    where, params = _build_filters(
        q=q,
        topics=topic,
        tags=tag,
        region=region,
        product=product,
        source=source,
        period=period,
        date_from=date_from,
        date_to=date_to,
        has_photo=has_photo,
    )
    params.update({"limit": limit, "offset": offset})

    if include_total:
        count_query = sql.SQL("SELECT COUNT(*) AS total FROM {table} n{where}").format(table=table, where=where)
        total = int(await fetch_val(count_query, params) or 0)
    else:
        total = -1

    query = sql.SQL(
        "SELECT {columns} FROM {table} n{where} ORDER BY {order_by} LIMIT %(limit)s OFFSET %(offset)s"
    ).format(columns=NEWS_LIST_COLUMNS, table=table, where=where, order_by=_order_by(sort_name))
    rows = await fetch_all(query, params)

    result = {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [row_to_news(row, for_list=True) for row in rows],
    }
    # БД удалённая + кэш per-process: держим дольше, чтобы при низком трафике
    # не бить по сети на каждый заход. Поиск чуть короче (свежесть важнее).
    return cache_set(cache_key, result, 120 if not q else 45)


_EVENT_STOPWORDS = {
    "и", "в", "во", "на", "по", "с", "со", "к", "ко", "за", "из", "от", "до", "для", "о", "об", "обо",
    "что", "как", "это", "его", "ее", "их", "или", "а", "но", "при", "над", "под", "после", "перед",
    "россии", "рф", "россия", "новости", "сегодня", "вчера", "сообщил", "сообщили", "заявил", "заявили",
    "года", "год", "месяца", "недели", "дня", "тыс", "млн", "руб", "рублей", "тонн", "тонны", "тонну",
}

_ROLE_LABELS = {
    "farmer": "Фермер",
    "processor": "Переработчик",
    "trader": "Трейдер",
    "agroholding": "Агрохолдинг",
    "exporter": "Экспортёр",
}


def _event_tokens(title: str, tags: list[str]) -> list[str]:
    raw = f"{title} {' '.join(tags[:12])}".casefold().replace("ё", "е")
    tokens = re.findall(r"[a-zа-я0-9]{3,}", raw)
    clean: list[str] = []
    for token in tokens:
        if token in _EVENT_STOPWORDS:
            continue
        if token.isdigit():
            continue
        if token not in clean:
            clean.append(token)
    return clean[:8]


def _event_key(row: dict[str, Any], news: dict[str, Any]) -> str:
    dt = row.get("date")
    # Окно в 3 дня: близкие публикации с одинаковым смыслом склеиваются в одно MVP-событие.
    day_bucket = "unknown"
    if isinstance(dt, datetime):
        day_bucket = str(dt.toordinal() // 3)
    tokens = _event_tokens(news.get("title") or "", news.get("tags") or [])
    if not tokens:
        tokens = [str(news.get("id"))]
    return "|".join(sorted(tokens[:6])) + "|" + day_bucket


def _short_event_summary(text: str | None) -> str:
    return (text or "").strip().replace("\n", " ")[:360].rstrip() + ("…" if text and len(text) > 360 else "")


def _role_impacts(tags: list[str], title: str) -> list[dict[str, str]]:
    corpus = " ".join([title, *tags]).casefold().replace("ё", "е")

    def has(*words: str) -> bool:
        return any(w.casefold().replace("ё", "е") in corpus for w in words)

    price = has("цена", "цены", "котиров", "fob", "cpt", "fca", "руб", "пшениц", "ячмен", "масло")
    export = has("экспорт", "экспортер", "порт", "тендер", "египет", "алжир", "турц", "gasc", "новороссийск")
    reg = has("пошлин", "минсельхоз", "фгис", "квот", "ндс", "регулятор", "закон", "правил")
    weather = has("засух", "осад", "погод", "замороз", "урожай", "прогноз", "ростов", "волгоград")
    deal = has("сделк", "закуп", "контракт", "деметр", "озк", "трейд")

    impacts: list[dict[str, str]] = []

    if weather:
        impacts.append({
            "role": "farmer", "label": _ROLE_LABELS["farmer"], "impact": "negative",
            "summary": "Погодный фактор может изменить урожайность и локальную цену.",
            "action_hint": "Проверьте риск по своим культурам и не принимайте решение только по средней цене.",
        })
    elif price or reg:
        impacts.append({
            "role": "farmer", "label": _ROLE_LABELS["farmer"], "impact": "watch",
            "summary": "Событие может повлиять на закупочные цены и окно продажи.",
            "action_hint": "Сравните текущую цену с плановой и рассмотрите частичную фиксацию объёма.",
        })
    else:
        impacts.append({
            "role": "farmer", "label": _ROLE_LABELS["farmer"], "impact": "neutral",
            "summary": "Прямое влияние на продажу урожая пока неочевидно.",
            "action_hint": "Оставьте в наблюдении, если событие связано с вашим регионом или культурой.",
        })

    if price or deal:
        impacts.append({
            "role": "processor", "label": _ROLE_LABELS["processor"], "impact": "watch",
            "summary": "Может измениться стоимость сырья и маржа переработки.",
            "action_hint": "Проверьте закупочные лимиты и альтернативные регионы поставки.",
        })
    else:
        impacts.append({
            "role": "processor", "label": _ROLE_LABELS["processor"], "impact": "neutral",
            "summary": "Пока нет явного сигнала по сырьевой базе.",
            "action_hint": "Следите за повторением события в ценовых тегах.",
        })

    if price or export or deal:
        impacts.append({
            "role": "trader", "label": _ROLE_LABELS["trader"], "impact": "positive" if export or deal else "watch",
            "summary": "Есть рыночный сигнал для арбитража, закупки или перепродажи.",
            "action_hint": "Проверьте спреды по базисам, портам и ближайшим срокам поставки.",
        })
    else:
        impacts.append({
            "role": "trader", "label": _ROLE_LABELS["trader"], "impact": "neutral",
            "summary": "Торговый сигнал слабый, нужно подтверждение ценой или объёмом.",
            "action_hint": "Дождитесь второго источника или связанного движения котировок.",
        })

    if export or reg:
        impacts.append({
            "role": "exporter", "label": _ROLE_LABELS["exporter"], "impact": "watch",
            "summary": "Может измениться netback, доступность портов или регуляторные условия.",
            "action_hint": "Проверьте контракты, пошлины, квоты и портовую логистику.",
        })
    else:
        impacts.append({
            "role": "exporter", "label": _ROLE_LABELS["exporter"], "impact": "neutral",
            "summary": "Прямой экспортный сигнал пока не выражен.",
            "action_hint": "Отслеживайте, появятся ли теги портов, пошлин или тендеров.",
        })

    if reg or weather or price:
        impacts.append({
            "role": "agroholding", "label": _ROLE_LABELS["agroholding"], "impact": "watch",
            "summary": "Событие может затронуть портфель регионов, культур или план продаж.",
            "action_hint": "Разложите влияние по регионам и культурам в портфеле.",
        })
    else:
        impacts.append({
            "role": "agroholding", "label": _ROLE_LABELS["agroholding"], "impact": "neutral",
            "summary": "Пока это общий информационный фон без явного портфельного риска.",
            "action_hint": "Вернитесь к событию при росте числа источников.",
        })

    return impacts


def _group_to_event(event_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    news_items = [row_to_news(r) for r in rows]
    news_items.sort(key=lambda n: ((n.get("views") or 0), n.get("date") or ""), reverse=True)
    main = news_items[0]

    dates = [r.get("date") for r in rows if isinstance(r.get("date"), datetime)]
    sources = Counter((n.get("source") or n.get("customer") or "источник").strip() for n in news_items)
    tags_counter: Counter[str] = Counter()
    topics_counter: Counter[str] = Counter()
    regions_counter: Counter[str] = Counter()
    products_counter: Counter[str] = Counter()
    for n in news_items:
        tags_counter.update(n.get("tags") or [])
        topics_counter.update(n.get("topics") or [])
        regions_counter.update(n.get("regions") or [])
        products_counter.update(n.get("products") or [])

    source_count = len([s for s in sources if s and s != "источник"]) or len(sources) or len(news_items)
    sigma = min(98, 52 + min(28, source_count * 7) + min(18, len(news_items) * 3))
    views = sum(int(n.get("views") or 0) for n in news_items)
    tags = [name for name, _ in tags_counter.most_common(14)]

    return {
        "id": event_id,
        "title": main.get("title") or "Событие без заголовка",
        "summary": _short_event_summary(main.get("summary") or main.get("text")),
        "date_from": min(dates).date().isoformat() if dates else None,
        "date_to": max(dates).date().isoformat() if dates else None,
        "news_count": len(news_items),
        "sources_count": int(source_count),
        "sigma": int(sigma),
        "views": int(views),
        "tags": tags,
        "topics": [name for name, _ in topics_counter.most_common(8)],
        "regions": [name for name, _ in regions_counter.most_common(8)],
        "products": [name for name, _ in products_counter.most_common(8)],
        "impacts": _role_impacts(tags, main.get("title") or ""),
        "sources": [
            {
                "id": int(n.get("id")),
                "title": n.get("title") or "Без заголовка",
                "source": n.get("source") or n.get("customer"),
                "date": n.get("date"),
                "link_site": n.get("link_site"),
            }
            for n in news_items[:6]
        ],
        "main_news_id": int(main.get("id")) if main.get("id") is not None else None,
    }


async def list_events(
    *,
    q: str | None,
    topic: list[str],
    tag: list[str],
    region: str | None,
    product: str | None,
    source: str | None,
    period: str | None,
    date_from: date | None = None,
    date_to: date | None = None,
    role: str | None = None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """MVP event layer поверх news_list без новых таблиц.

    Группирует похожие публикации в событие по нормализованным токенам заголовка/тегов
    и 3-дневному окну. Это не заменяет будущую таблицу events, но уже даёт продуктовую
    сущность: событие, источники, σ, теги и смысл для ролей.
    """
    table = await news_table_identifier()
    where, params = _build_filters(
        q=q,
        topics=topic,
        tags=tag,
        region=region,
        product=product,
        source=source,
        period=period,
        date_from=date_from,
        date_to=date_to,
    )
    fetch_limit = min(max((limit + offset) * 10, 250), 1200)
    params.update({"limit": fetch_limit})
    query = sql.SQL(
        """
        SELECT {columns}
        FROM {table} n
        {where}
        ORDER BY n.date DESC NULLS LAST, COALESCE(n.views, 0) DESC, n.id DESC
        LIMIT %(limit)s
        """
    ).format(columns=NEWS_COLUMNS, table=table, where=where)
    rows = await fetch_all(query, params)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        news = row_to_news(row)
        raw_key = _event_key(row, news)
        event_id = hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:12]
        groups[event_id].append(row)

    events = [_group_to_event(event_id, group_rows) for event_id, group_rows in groups.items()]

    if role:
        # Для выбранной роли поднимаем наверх события с ненейтральным влиянием.
        def role_score(event: dict[str, Any]) -> int:
            impact = next((x for x in event["impacts"] if x["role"] == role), None)
            if not impact:
                return 0
            return {"positive": 3, "negative": 3, "watch": 2, "neutral": 0}.get(impact["impact"], 0)
    else:
        def role_score(event: dict[str, Any]) -> int:
            return 0

    events.sort(
        key=lambda e: (
            role_score(e),
            e["sigma"],
            e["sources_count"],
            e["news_count"],
            e["views"],
            e["date_to"] or "",
        ),
        reverse=True,
    )

    total = len(events)
    page = events[offset: offset + limit]
    return {"total": total, "limit": limit, "offset": offset, "items": page}


async def featured_news(limit: int) -> list[dict[str, Any]]:
    """Топ популярных новостей за последние 24 часа.

    Окно считаем от самой свежей качественной новости в БД, а не от системного
    времени сервера: так блок корректно работает и на исторических выгрузках.
    """
    cache_key = ("featured_news_top24h", limit)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    table = await news_table_identifier()
    query = sql.SQL(
        """
        WITH anchor AS (
            SELECT MAX(n.date) AS max_date
            FROM {table} n
            WHERE {quality}
        )
        SELECT {columns}
        FROM {table} n
        CROSS JOIN anchor a
        WHERE {quality}
          AND a.max_date IS NOT NULL
          AND n.date >= a.max_date - INTERVAL '24 hours'
          AND n.date <= a.max_date
        ORDER BY
            COALESCE(n.views, 0) DESC,
            COALESCE(n.subscribers, 0) DESC,
            n.date DESC NULLS LAST,
            n.id DESC
        LIMIT %(limit)s
        """
    ).format(columns=NEWS_LIST_COLUMNS, table=table, quality=QUALITY_WHERE)
    rows = await fetch_all(query, {"limit": limit})
    result = [row_to_news(row, for_list=True) for row in rows]
    return cache_set(cache_key, result, 120)


async def top_read_news(limit: int) -> list[dict[str, Any]]:
    cache_key = ("top_read_news", limit)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    table = await news_table_identifier()
    query = sql.SQL(
        """
        SELECT {columns}
        FROM {table} n
        WHERE {quality}
        ORDER BY COALESCE(n.views, 0) DESC, n.date DESC NULLS LAST, n.id DESC
        LIMIT %(limit)s
        """
    ).format(columns=NEWS_LIST_COLUMNS, table=table, quality=QUALITY_WHERE)
    rows = await fetch_all(query, {"limit": limit})
    result = [row_to_news(row, for_list=True) for row in rows]
    return cache_set(cache_key, result, 180)


_GENERIC_JSON_KEYS_SQL = [
    "name", "title", "value", "label", "text", "region", "product", "topic", "tag", "id", "code",
    "count", "type", "url", "link", "date", "source",
]


def _facet_values_subquery(fields: list[str]) -> sql.Composed:
    """Возвращает SQL-выборку jsonb-значений из нужных полей news_list.

    Дальше PostgreSQL сам разворачивает jsonb в теги/регионы/продукты и считает
    частоты. Это заменяет старую схему, где backend вытаскивал все строки
    news_list в Python только ради подсчёта facet'ов.
    """
    parts = [
        sql.SQL("SELECT n.{field} AS value FROM {table} n WHERE {quality}").format(
            field=sql.Identifier(field),
            table=sql.Placeholder("__table__"),
            quality=sql.Placeholder("__quality__"),
        )
        for field in fields
    ]
    # Заглушки __table__/__quality__ заменяются в вызывающей функции через str не будут
    # использоваться; оставлено только для type-checking. Реальная функция ниже
    # строит parts напрямую, потому что table/quality являются Composable.
    return sql.SQL(" UNION ALL ").join(parts)


def _news_json_values_sql(table: sql.Composable, fields: list[str]) -> sql.Composed:
    parts = [
        sql.SQL("SELECT n.{field} AS value FROM {table} n WHERE {quality}").format(
            field=sql.Identifier(field),
            table=table,
            quality=QUALITY_WHERE,
        )
        for field in fields
    ]
    return sql.SQL(" UNION ALL ").join(parts)


async def _db_json_facets(table: sql.Composable, fields: list[str], *, limit: int = 40) -> list[dict[str, Any]]:
    """Считает facet'ы jsonb-полей на стороне PostgreSQL.

    Поддерживаются типичные форматы из news_list:
    - ["Пшеница", "Экспорт"]
    - {"ЦФО": "", "Линия": ""}
    - [{"name": "Пшеница"}, {"value": "Экспорт"}]

    Для словарей вида {"ЦФО": ""} значимым считается ключ. Для объектов
    с generic-полями name/value/title/label/text берётся текстовое значение.
    """
    values_sql = _news_json_values_sql(table, fields)
    # Важно: не используем sql.SQL(...).format(...) для всего запроса.
    # Внутри SQL есть JSON/jsonpath-литералы с фигурными скобками (`{}`),
    # а psycopg.sql.format воспринимает любые `{...}` как placeholders.
    # Поэтому вставляем только values_sql через композицию SQL-объектов.
    query = (
        sql.SQL(
            """
            WITH raw AS (
            """
        )
        + values_sql
        + sql.SQL(
            """
            ), tokens AS (
                SELECT kv.key::text AS name
                FROM raw r
                CROSS JOIN LATERAL jsonb_each(
                    CASE WHEN jsonb_typeof(r.value) = 'object' THEN r.value ELSE '{}'::jsonb END
                ) AS kv(key, value)
                WHERE lower(kv.key) <> ALL(%(generic_keys)s)

                UNION ALL

                SELECT kv.key::text AS name
                FROM raw r
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN jsonb_typeof(r.value) = 'array' THEN r.value ELSE '[]'::jsonb END
                ) AS elem(value)
                CROSS JOIN LATERAL jsonb_each(
                    CASE WHEN jsonb_typeof(elem.value) = 'object' THEN elem.value ELSE '{}'::jsonb END
                ) AS kv(key, value)
                WHERE lower(kv.key) <> ALL(%(generic_keys)s)

                UNION ALL

                SELECT val #>> '{}' AS name
                FROM raw r
                CROSS JOIN LATERAL jsonb_path_query(
                    COALESCE(r.value, 'null'::jsonb),
                    '$.** ? (@.type() == "string" || @.type() == "number")'::jsonpath
                ) AS val
            )
            SELECT name, COUNT(*)::int AS count
            FROM tokens
            WHERE name IS NOT NULL
              AND BTRIM(name) <> ''
              AND lower(BTRIM(name)) NOT IN ('null', 'none', 'true', 'false')
              AND lower(BTRIM(name)) <> ALL(%(generic_keys)s)
              AND NOT (BTRIM(name) ~* '^(https?://|www[.]|t[.]me/)')
            GROUP BY name
            ORDER BY count DESC, lower(name) ASC
            LIMIT %(limit)s
            """
        )
    )
    rows = await fetch_all(query, {"generic_keys": _GENERIC_JSON_KEYS_SQL, "limit": limit})
    return [{"name": str(r["name"]), "count": int(r["count"])} for r in rows]


async def _db_text_facets(table: sql.Composable, field: str, *, limit: int = 40) -> list[dict[str, Any]]:
    query = sql.SQL(
        """
        SELECT BTRIM(COALESCE(n.{field}, '')) AS name, COUNT(*)::int AS count
        FROM {table} n
        WHERE {quality}
          AND NULLIF(BTRIM(COALESCE(n.{field}, '')), '') IS NOT NULL
        GROUP BY BTRIM(COALESCE(n.{field}, ''))
        ORDER BY count DESC, lower(BTRIM(COALESCE(n.{field}, ''))) ASC
        LIMIT %(limit)s
        """
    ).format(field=sql.Identifier(field), table=table, quality=QUALITY_WHERE)
    rows = await fetch_all(query, {"limit": limit})
    return [{"name": str(r["name"]), "count": int(r["count"])} for r in rows]


async def _events_total_count() -> int:
    """Общее количество подготовленных событий.

    Не создаём таблицы из meta-запроса: если worker ещё не запускался, просто
    возвращаем 0.
    """
    from app.config import settings

    exists = await fetch_val("SELECT to_regclass(%(table_name)s)::text", {"table_name": f"{settings.events_schema}.events"})
    if not exists:
        return 0
    value = await fetch_val(
        sql.SQL("SELECT COUNT(*)::int FROM {}.events WHERE status = 'active' AND sources_count >= %(event_min_sources)s").format(sql.Identifier(settings.events_schema)),
        {"event_min_sources": settings.event_min_sources},
    )
    return int(value or 0)



async def _event_json_facets(field: str, *, limit: int = 80) -> list[dict[str, Any]]:
    """Быстрые facet'ы из уже подготовленных событий.

    Это быстрее, чем сканировать всю news_list и разбирать все JSONB-поля.
    Для главной страницы нам достаточно справочников по событиям: они уже
    очищены worker-ом и содержат нормализованные tags/topics/regions/products.
    """
    from app.config import settings
    from app.services.event_tables import event_table_identifier

    exists = await fetch_val(
        "SELECT to_regclass(%(table_name)s)::text",
        {"table_name": f"{settings.events_schema}.events"},
    )
    if not exists:
        return []

    query = sql.SQL(
        """
        SELECT BTRIM(elem.value)::text AS name, COUNT(*)::int AS count
        FROM {events} e
        CROSS JOIN LATERAL jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(e.{field}) = 'array' THEN e.{field} ELSE '[]'::jsonb END
        ) AS elem(value)
        WHERE e.status = 'active'
          AND BTRIM(elem.value) <> ''
          AND lower(BTRIM(elem.value)) NOT IN ('null', 'none', 'true', 'false')
          AND NOT (BTRIM(elem.value) ~* '^(https?://|www[.]|t[.]me/)')
        GROUP BY BTRIM(elem.value)
        ORDER BY count DESC, lower(BTRIM(elem.value)) ASC
        LIMIT %(limit)s
        """
    ).format(events=event_table_identifier("events"), field=sql.Identifier(field))
    rows = await fetch_all(query, {"limit": limit})
    return [{"name": str(r["name"]), "count": int(r["count"])} for r in rows]


async def _event_text_facets(field: str, *, limit: int = 80) -> list[dict[str, Any]]:
    from app.config import settings
    from app.services.event_tables import event_table_identifier

    exists = await fetch_val(
        "SELECT to_regclass(%(table_name)s)::text",
        {"table_name": f"{settings.events_schema}.event_sources"},
    )
    if not exists:
        return []

    query = sql.SQL(
        """
        SELECT BTRIM(COALESCE(s.{field}, '')) AS name, COUNT(*)::int AS count
        FROM {sources} s
        WHERE NULLIF(BTRIM(COALESCE(s.{field}, '')), '') IS NOT NULL
          AND NOT (BTRIM(COALESCE(s.{field}, '')) ~* '^(https?://|www[.]|t[.]me/)$')
        GROUP BY BTRIM(COALESCE(s.{field}, ''))
        ORDER BY count DESC, lower(BTRIM(COALESCE(s.{field}, ''))) ASC
        LIMIT %(limit)s
        """
    ).format(sources=event_table_identifier("event_sources"), field=sql.Identifier(field))
    rows = await fetch_all(query, {"limit": limit})
    return [{"name": str(r["name"]), "count": int(r["count"])} for r in rows]



async def _topic_mark_facets(*, limit: int = 80) -> list[dict[str, Any]]:
    """Быстрые темы из нормализованной таблицы news_topic_marks."""
    exists = await fetch_val(
        "SELECT to_regclass(%(table_name)s)::text",
        {"table_name": f"{settings.events_schema}.news_topic_marks"},
    )
    if not exists:
        return []
    rows = await fetch_all(
        sql.SQL(
            """
            SELECT topic AS name, COUNT(DISTINCT news_id)::int AS count
            FROM {marks}
            WHERE NULLIF(BTRIM(topic), '') IS NOT NULL
            GROUP BY topic
            ORDER BY count DESC, lower(topic) ASC
            LIMIT %(limit)s
            """
        ).format(marks=_topic_marks_table()),
        {"limit": limit},
    )
    return [{"name": str(r["name"]), "count": int(r["count"])} for r in rows]


async def _topic_daily_facets(*, limit: int = 80) -> list[dict[str, Any]]:
    """Самые быстрые темы из готового дневного агрегата topic_daily_stats."""
    exists = await fetch_val(
        "SELECT to_regclass(%(table_name)s)::text",
        {"table_name": f"{settings.events_schema}.topic_daily_stats"},
    )
    if not exists:
        return []
    rows = await fetch_all(
        sql.SQL(
            """
            SELECT topic AS name, SUM(news_count)::int AS count
            FROM {daily}
            WHERE NULLIF(BTRIM(topic), '') IS NOT NULL
            GROUP BY topic
            ORDER BY count DESC, lower(topic) ASC
            LIMIT %(limit)s
            """
        ).format(daily=_topic_daily_stats_table()),
        {"limit": limit},
    )
    return [{"name": str(r["name"]), "count": int(r["count"])} for r in rows]


async def news_meta() -> dict[str, Any]:
    """Быстрая meta для фильтров.

    Счётчики тегов берём из таблицы событий, а не из всей news_list. Это делает
    главную страницу быстрее и убирает тяжёлый JSONB-scan по сырой ленте.
    news_total считаем простым COUNT(*) по news_list, events_total — по events.
    """
    cache_key = ("news_meta_topic_daily_v2",)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    import asyncio

    table = await news_table_identifier()

    total_task = fetch_val(sql.SQL("SELECT COUNT(*)::int FROM {table}").format(table=table))
    events_total_task = _events_total_count()
    topics_task = _topic_daily_facets(limit=80)
    regions_task = _event_json_facets("regions", limit=120)
    products_task = _event_json_facets("products", limit=120)
    sources_task = _event_text_facets("source", limit=80)

    total, events_total, topics, regions, products, sources = await asyncio.gather(
        total_task,
        events_total_task,
        topics_task,
        regions_task,
        products_task,
        sources_task,
    )
    # Fallback на marks/events, если дневный агрегат ещё не пересобран после деплоя.
    if not topics:
        topics = await _topic_mark_facets(limit=80)
    if not topics:
        topics = await _event_json_facets("topics", limit=80)

    total_int = int(total or 0)
    result = {
        "total": total_int,
        "news_total": total_int,
        "events_total": int(events_total or 0),
        "topics": topics,
        "regions": regions,
        "products": products,
        "tags": [],
        "sources": sources,
        "customers": [],
    }
    return cache_set(cache_key, result, 600)


async def _timeline_from_daily_stats(*, days: int) -> dict[str, Any] | None:
    """Самый быстрый путь для графика без активных фильтров.

    Читает готовые агрегаты:
    - topic_daily_totals: total новостей по дням;
    - topic_daily_stats: распределение тем по дням.

    Это убирает JOIN news_list + GROUP BY на каждый заход на главную.
    """
    totals_exists = await fetch_val(
        "SELECT to_regclass(%(table_name)s)::text",
        {"table_name": f"{settings.events_schema}.topic_daily_totals"},
    )
    stats_exists = await fetch_val(
        "SELECT to_regclass(%(table_name)s)::text",
        {"table_name": f"{settings.events_schema}.topic_daily_stats"},
    )
    if not totals_exists or not stats_exists:
        return None

    anchor_value = await fetch_val(
        sql.SQL("SELECT MAX(bucket_date) FROM {totals}").format(totals=_topic_daily_totals_table())
    )
    if not isinstance(anchor_value, date):
        return None

    today = anchor_value
    date_from_date = today - timedelta(days=days - 1)
    date_to_date = today
    params = {"date_from": date_from_date, "date_to": date_to_date}

    rows = await fetch_all(
        sql.SQL(
            """
            WITH total_rows AS (
                SELECT bucket_date AS day, news_count::int AS total
                FROM {totals}
                WHERE bucket_date >= %(date_from)s AND bucket_date <= %(date_to)s
            ),
            topic_rows AS (
                SELECT bucket_date AS day, topic, news_count::int AS count
                FROM {stats}
                WHERE bucket_date >= %(date_from)s AND bucket_date <= %(date_to)s
            ),
            global_rows AS (
                SELECT topic, SUM(news_count)::int AS count
                FROM {stats}
                WHERE bucket_date >= %(date_from)s AND bucket_date <= %(date_to)s
                GROUP BY topic
                ORDER BY count DESC, lower(topic) ASC
                LIMIT 40
            )
            SELECT 'total' AS kind, day::text AS day, NULL::text AS name, total AS count
            FROM total_rows
            UNION ALL
            SELECT 'topic' AS kind, day::text AS day, topic AS name, count
            FROM topic_rows
            UNION ALL
            SELECT 'global' AS kind, NULL::text AS day, topic AS name, count
            FROM global_rows
            ORDER BY kind, day NULLS LAST, count DESC, name
            """
        ).format(totals=_topic_daily_totals_table(), stats=_topic_daily_stats_table()),
        params,
    )

    by_day: dict[date, dict[str, Any]] = {}
    for i in range(days):
        d = today - timedelta(days=days - 1 - i)
        by_day[d] = {"date": d, "total": 0, "topics": defaultdict(int), "related": []}

    topics_total: dict[str, int] = {}
    for row in rows:
        kind = row.get("kind")
        if kind == "global":
            name = str(row.get("name") or "").strip()
            if name:
                topics_total[name] = int(row.get("count") or 0)
            continue

        day_raw = row.get("day")
        if not day_raw:
            continue
        try:
            d = datetime.strptime(str(day_raw), "%Y-%m-%d").date()
        except ValueError:
            continue
        if d not in by_day:
            continue

        if kind == "total":
            by_day[d]["total"] = int(row.get("count") or 0)
        elif kind == "topic":
            name = str(row.get("name") or "остальное").strip() or "остальное"
            by_day[d]["topics"][name] += int(row.get("count") or 0)

    items = []
    total = 0
    for d in sorted(by_day.keys()):
        item = by_day[d]
        total += int(item["total"])
        items.append(
            {
                "date": d.isoformat(),
                "total": int(item["total"]),
                "topics": dict(sorted(item["topics"].items(), key=lambda x: (-x[1], x[0]))),
                "related": [],
            }
        )

    return {
        "days": days,
        "date_from": date_from_date.isoformat(),
        "date_to": date_to_date.isoformat(),
        "total": total,
        "avg_per_day": round(total / days, 2),
        "topics": facet_list(topics_total, limit=40),
        "items": items,
        "source": "topic_daily_stats",
    }


async def timeline(
    *,
    days: int,
    topic: list[str],
    tag: list[str],
    region: str | None,
    product: str | None,
    source: str | None,
) -> dict[str, Any]:
    """Быстрый график активности по темам.

    Важно: график больше не считает сырые tag/extra_tag/object. Только поле topics.
    Агрегация выполняется в PostgreSQL и возвращает уже сгруппированные день/тема,
    чтобы backend не тащил десятки тысяч строк в Python.
    """
    effective_topics: list[str] = []
    for value in [*(topic or []), *(tag or [])]:
        if value and value not in effective_topics:
            effective_topics.append(value)

    cache_key = ("timeline_topic_daily_v2", days, frozen_list(effective_topics), region or "", product or "", source or "")
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    days = max(1, min(days, 365))

    # Самый частый сценарий главной: график без активных фильтров.
    # В этом случае читаем готовую маленькую таблицу topic_daily_stats,
    # а не пересчитываем JOIN по news_list/news_topic_marks.
    if not effective_topics and not region and not product and not source:
        fast_result = await _timeline_from_daily_stats(days=days)
        if fast_result is not None:
            return cache_set(cache_key, fast_result, 600)

    table = await news_table_identifier()
    where, params = _build_filters(
        topics=effective_topics,
        tags=[],
        region=region,
        product=product,
        source=source,
    )

    anchor_value = await fetch_val(
        sql.SQL("SELECT MAX(n.date)::date FROM {table} n {where}").format(table=table, where=where),
        dict(params),
    )
    today = anchor_value if isinstance(anchor_value, date) else date.today()

    date_from_date = today - timedelta(days=days - 1)
    date_to_date = today
    date_from_dt = datetime.combine(date_from_date, time.min)
    date_to_dt = datetime.combine(today + timedelta(days=1), time.min)
    params.update({"date_from": date_from_dt, "date_to": date_to_dt})
    full_where = where + sql.SQL(" AND n.date >= %(date_from)s AND n.date < %(date_to)s")

    # Один SQL-запрос возвращает totals и темы, но теперь темы берутся
    # из нормализованной таблицы harvester_news.news_topic_marks.
    # Это быстрее, чем jsonb_array_elements/jsonb_each по news_list при каждом запросе графика.
    marks = _topic_marks_table()
    filtered_sql = sql.SQL(
        """
            SELECT n.id, n.date::date AS day
            FROM {table} n
        """
    ).format(table=table) + full_where

    query = (
        sql.SQL("WITH filtered AS MATERIALIZED (")
        + filtered_sql
        + sql.SQL(
            """
            ),
            total_rows AS (
                SELECT day, COUNT(*)::int AS total
                FROM filtered
                GROUP BY day
            ),
            topic_rows AS (
                SELECT
                    f.day,
                    COALESCE(NULLIF(BTRIM(m.topic), ''), 'остальное') AS topic,
                    COUNT(DISTINCT f.id)::int AS count
                FROM filtered f
                LEFT JOIN 
            """
        )
        + marks
        + sql.SQL(
            """
             m ON m.news_id = f.id
                GROUP BY f.day, COALESCE(NULLIF(BTRIM(m.topic), ''), 'остальное')
            ),
            global_rows AS (
                SELECT topic, SUM(count)::int AS count
                FROM topic_rows
                WHERE topic <> 'остальное'
                GROUP BY topic
                ORDER BY count DESC, lower(topic) ASC
                LIMIT 40
            )
            SELECT 'total' AS kind, day::text AS day, NULL::text AS name, total AS count
            FROM total_rows
            UNION ALL
            SELECT 'topic' AS kind, day::text AS day, topic AS name, count
            FROM topic_rows
            UNION ALL
            SELECT 'global' AS kind, NULL::text AS day, topic AS name, count
            FROM global_rows
            ORDER BY kind, day NULLS LAST, count DESC, name
            """
        )
    )

    rows = await fetch_all(query, params)

    by_day: dict[date, dict[str, Any]] = {}
    for i in range(days):
        d = today - timedelta(days=days - 1 - i)
        by_day[d] = {"date": d, "total": 0, "topics": defaultdict(int), "related": []}

    topics_total: dict[str, int] = {}
    for row in rows:
        kind = row.get("kind")
        if kind == "global":
            name = str(row.get("name") or "").strip()
            if name:
                topics_total[name] = int(row.get("count") or 0)
            continue

        day_raw = row.get("day")
        if not day_raw:
            continue
        try:
            d = datetime.strptime(str(day_raw), "%Y-%m-%d").date()
        except ValueError:
            continue
        if d not in by_day:
            continue

        if kind == "total":
            by_day[d]["total"] = int(row.get("count") or 0)
        elif kind == "topic":
            name = str(row.get("name") or "остальное").strip() or "остальное"
            by_day[d]["topics"][name] += int(row.get("count") or 0)

    items = []
    total = 0
    for d in sorted(by_day.keys()):
        item = by_day[d]
        total += int(item["total"])
        items.append(
            {
                "date": d.isoformat(),
                "total": int(item["total"]),
                "topics": dict(sorted(item["topics"].items(), key=lambda x: (-x[1], x[0]))),
                "related": [],
            }
        )

    result = {
        "days": days,
        "date_from": date_from_date.isoformat(),
        "date_to": date_to_date.isoformat(),
        "total": total,
        "avg_per_day": round(total / days, 2),
        "topics": facet_list(topics_total, limit=40),
        "items": items,
    }
    return cache_set(cache_key, result, 300)


async def news_by_id(news_id: int) -> dict[str, Any] | None:
    table = await news_table_identifier()
    query = sql.SQL(
        """
        SELECT {columns}
        FROM {table} n
        WHERE n.id = %(id)s
          AND {quality}
        LIMIT 1
        """
    ).format(columns=NEWS_COLUMNS, table=table, quality=QUALITY_WHERE)
    row = await fetch_one(query, {"id": news_id})
    return row_to_news(row) if row else None


async def similar_news(news_id: int, limit: int = 3) -> list[dict[str, Any]]:
    """Возвращает новости с пересечением тем, близкие по дате."""
    cache_key = ("similar_news", news_id, limit)
    cached = cache_get(cache_key)
    if cached is not None:
        return list(cached)

    ref = await news_by_id(news_id)
    if not ref:
        return []

    ref_topics = ref.get("topics") or []
    if isinstance(ref_topics, dict):
        topic_keys = list(ref_topics.keys())
    elif isinstance(ref_topics, list):
        topic_keys = [t for t in ref_topics if t]
    else:
        topic_keys = []

    ref_date = ref.get("date")
    if isinstance(ref_date, str):
        try:
            ref_date = datetime.fromisoformat(ref_date[:19])
        except ValueError:
            ref_date = datetime.now()
    if not isinstance(ref_date, datetime):
        ref_date = datetime.now()

    date_from = ref_date - timedelta(days=14)
    date_to = ref_date + timedelta(days=3)

    table = await news_table_identifier()

    if topic_keys:
        query = sql.SQL(
            """
            SELECT {cols}
            FROM {table} n
            WHERE n.id != %(news_id)s
              AND {quality}
              AND n.date BETWEEN %(date_from)s AND %(date_to)s
              AND (
                CASE WHEN jsonb_typeof(n.topics) = 'object'
                     THEN n.topics ELSE %(empty)s::jsonb END
              ) ?| %(topic_keys)s
            ORDER BY n.date DESC
            LIMIT %(limit)s
            """
        ).format(cols=NEWS_LIST_COLUMNS, table=table, quality=QUALITY_WHERE)
        params: dict[str, Any] = {
            "news_id": news_id,
            "date_from": date_from,
            "date_to": date_to,
            "topic_keys": topic_keys,
            "empty": "{}",
            "limit": limit * 4,  # с запасом для дедупликации
        }
    else:
        # Нет тем — похожие по источнику
        query = sql.SQL(
            """
            SELECT {cols}
            FROM {table} n
            WHERE n.id != %(news_id)s
              AND {quality}
              AND n.date BETWEEN %(date_from)s AND %(date_to)s
              AND n.source = %(source)s
            ORDER BY n.date DESC
            LIMIT %(limit)s
            """
        ).format(cols=NEWS_LIST_COLUMNS, table=table, quality=QUALITY_WHERE)
        params = {
            "news_id": news_id,
            "date_from": date_from,
            "date_to": date_to,
            "source": ref.get("source"),
            "limit": limit * 4,
        }

    rows = await fetch_all(query, params)

    # Дедупликация по нормализованному заголовку — убираем дубли с одинаковым текстом
    seen_titles: set[str] = set()
    result: list[dict[str, Any]] = []
    for r in rows:
        item = row_to_news(r)
        title_key = re.sub(r"\s+", " ", (item.get("title") or "").strip().casefold())[:80]
        if title_key and title_key not in seen_titles:
            seen_titles.add(title_key)
            result.append(item)
        if len(result) >= limit:
            break

    cache_set(cache_key, frozen_list(result), 120)
    return result


async def debug_db() -> dict[str, Any]:
    row = await fetch_one(
        """
        SELECT current_database() AS database,
               current_user AS username,
               current_schema() AS current_schema,
               inet_server_addr()::text AS server_addr,
               inet_server_port() AS server_port
        """
    )
    table_rows = await fetch_all(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_name = 'news_list'
          AND table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY table_schema
        """
    )
    try:
        schema = await resolve_news_schema()
    except Exception as e:
        schema = None
        error = str(e)
    else:
        error = None

    return {
        "connection": row,
        "resolved_news_schema": schema,
        "news_list_tables": table_rows,
        "error": error,
    }
