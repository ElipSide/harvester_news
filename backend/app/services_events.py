from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import hashlib
import logging
import math
import re
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

from app.config import settings
from app.db.db_ext import get_conn, news_table_identifier
from app.db.db_ext_func import fetch_all, fetch_one, fetch_val
from app.services.event_tables import ensure_event_schema, event_table_identifier
from app.services_news import NEWS_COLUMNS, QUALITY_WHERE, _build_filters, _role_impacts
from app.services_cache import cache_get, cache_set, frozen_list, cache_delete_prefix
from app.utils.news_utils import extract_tags, json_to_list, row_to_news

logger = logging.getLogger(__name__)

_ROLE_LABELS = {
    "farmer": "Фермер",
    "processor": "Переработчик",
    "trader": "Трейдер",
    "agroholding": "Агрохолдинг",
    "exporter": "Экспортёр",
}
_ALLOWED_IMPACTS = {"positive", "negative", "neutral", "watch"}

# Для построения событий исключаем дайджесты/подборки и сервисные агрегаторы.
# Они могут быть полезны как обычная публикация, но в event-worker они становятся
# мостом и склеивают несколько разных инфоповодов в один кластер.
_EVENT_EXCLUDED_TITLE_PATTERNS = [
    "%дайджест%",
    "%самое интересное за день%",
    "%самое интересное за неделю%",
    "%главные новости%",
    "%новости рынка на%",
    "%итоги дня%",
    "%итоги недели%",
    "%подборка новостей%",
    "%результаты состоявшихся торгов%",
    "%главпахарь: самое интересное%",
    "%главагроном: самое интересное%",
]

_STOPWORDS = {
    "и", "в", "во", "на", "по", "с", "со", "к", "ко", "за", "из", "от", "до", "для", "о", "об", "обо",
    "что", "как", "это", "его", "ее", "её", "их", "или", "а", "но", "при", "над", "под", "после", "перед",
    "россии", "рф", "россия", "новости", "сегодня", "вчера", "сообщил", "сообщили", "заявил", "заявили",
    "года", "год", "месяца", "недели", "дня", "тыс", "млн", "руб", "рублей", "тонн", "тонны", "тонну",
    "будет", "были", "было", "быть", "есть", "также", "уже", "еще", "ещё", "может", "могут", "стал", "стала",
}
_TOKEN_RE = re.compile(r"[a-zа-яё0-9]{3,}", re.IGNORECASE)
_SENT_RE = re.compile(r"(?<=[.!?…])\s+|\n+", re.UNICODE)


def _topic_norm(value: str) -> str:
    return (value or "").strip().casefold().replace("ё", "е")


def _topic_marks_table() -> sql.Composed:
    return sql.Identifier(settings.events_schema, "news_topic_marks")


def _event_news_state_table() -> sql.Composed:
    return sql.Identifier(settings.events_schema, "event_news_state")


def _clean_text(value: Any, max_len: int = 600) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:max_len].strip()


def _as_str_list(value: Any, limit: int = 20) -> list[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, tuple):
        raw = list(value)
    elif value is None:
        raw = []
    else:
        raw = [value]
    result: list[str] = []
    for item in raw:
        text = _clean_text(item, 80)
        if not text:
            continue
        if text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _normalize_impact(item: dict[str, Any]) -> dict[str, str] | None:
    role = _clean_text(item.get("role"), 40)
    if role not in _ROLE_LABELS:
        return None
    impact = _clean_text(item.get("impact"), 20)
    if impact not in _ALLOWED_IMPACTS:
        impact = "watch"
    return {
        "role": role,
        "label": _ROLE_LABELS[role],
        "impact": impact,
        "summary": _clean_text(item.get("summary"), 260),
        "action_hint": _clean_text(item.get("action_hint"), 260),
    }


def _tokens(text: str, limit: int = 80) -> list[str]:
    words: list[str] = []
    for m in _TOKEN_RE.finditer((text or "").casefold().replace("ё", "е")):
        w = m.group(0)
        if w in _STOPWORDS:
            continue
        if w.isdigit():
            continue
        words.append(w)
    # Сохраняем порядок, но убираем повторения
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= limit:
            break
    return out


def _news_tags(row: dict[str, Any]) -> list[str]:
    return extract_tags(row.get("tag"), row.get("extra_tag"), row.get("object"), row.get("topics"), row.get("regions"), row.get("products"))


def _jsonish_to_list(value: Any, limit: int = 20) -> list[str]:
    return _as_str_list(value, limit)


def _news_record(row: dict[str, Any]) -> dict[str, Any]:
    news = row_to_news(row)
    tags = _news_tags(row)
    title = _clean_text(news.get("title"), 240)
    text = _clean_text(news.get("text") or news.get("summary"), 2000)
    token_list = _tokens(" ".join([title, text, " ".join(tags)]), 100)
    return {
        "row": row,
        "news": news,
        "id": int(news.get("id") or row.get("id")),
        "date": row.get("date"),
        "title": title,
        "text": text,
        "source": news.get("source") or news.get("customer") or "",
        "views": int(news.get("views") or 0),
        "tags": tags,
        "tokens": set(token_list),
        "token_list": token_list,
        "topics": _as_str_list(news.get("topics"), 20),
        "regions": _as_str_list(news.get("regions") or row.get("regions"), 20),
        "products": _as_str_list(news.get("products") or row.get("products"), 20),
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _date_gap_days(a: Any, b: Any) -> int:
    if not isinstance(a, datetime) or not isinstance(b, datetime):
        return 9999
    return abs((a.date() - b.date()).days)


@dataclass
class _ClusterProfile:
    """Инкрементально обновляемый профиль кластера.

    Хранит счётчики токенов/тегов и наборы токенов заголовков, собранные при
    добавлении каждого элемента. Это позволяет не пересчитывать Counter с нуля
    при каждом вызове _cluster_similarity.
    """
    token_counter: Counter = field(default_factory=Counter)
    tag_counter: Counter = field(default_factory=Counter)
    title_token_sets: list[set[str]] = field(default_factory=list)
    dates: list[datetime] = field(default_factory=list)

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> "_ClusterProfile":
        p = cls()
        p.token_counter.update(rec["token_list"][:40])
        p.tag_counter.update(t.casefold().replace("ё", "е") for t in rec["tags"][:20])
        p.title_token_sets.append(set(_tokens(rec["title"], 30)))
        if isinstance(rec.get("date"), datetime):
            p.dates.append(rec["date"])
        return p

    def update(self, rec: dict[str, Any]) -> None:
        self.token_counter.update(rec["token_list"][:40])
        self.tag_counter.update(t.casefold().replace("ё", "е") for t in rec["tags"][:20])
        self.title_token_sets.append(set(_tokens(rec["title"], 30)))
        if isinstance(rec.get("date"), datetime):
            self.dates.append(rec["date"])


def _profile_similarity(rec: dict[str, Any], profile: _ClusterProfile, rec_title_tokens: set[str]) -> float:
    cluster_tokens = {w for w, _ in profile.token_counter.most_common(50)}
    cluster_tags = {w for w, _ in profile.tag_counter.most_common(30)}
    rec_tags = {t.casefold().replace("ё", "е") for t in rec["tags"][:30]}

    token_score = _jaccard(rec["tokens"], cluster_tokens)
    tag_score = _jaccard(rec_tags, cluster_tags)
    title_score = max(
        (_jaccard(rec_title_tokens, tts) for tts in profile.title_token_sets), default=0.0
    )

    date_score = 0.0
    if profile.dates and isinstance(rec.get("date"), datetime):
        min_gap = min(_date_gap_days(rec["date"], d) for d in profile.dates)
        if min_gap <= settings.event_cluster_window_days:
            date_score = 1.0 - (min_gap / max(1, settings.event_cluster_window_days + 1))

    return 0.48 * token_score + 0.30 * tag_score + 0.12 * title_score + 0.10 * date_score


def cluster_news_rows(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Локальный retrieval/cluster без платных LLM и внешних сервисов.

    Профиль каждого кластера обновляется инкрементально: Counter не пересчитывается
    с нуля при каждом сравнении, а лишь дополняется токенами нового элемента.
    """
    records = [_news_record(r) for r in rows]
    records.sort(key=lambda r: (r["date"] or datetime.min, r["views"]), reverse=True)

    clusters: list[list[dict[str, Any]]] = []
    profiles: list[_ClusterProfile] = []

    for rec in records:
        rec_title_tokens = set(_tokens(rec["title"], 30))
        best_i = -1
        best_score = 0.0
        for idx, profile in enumerate(profiles):
            score = _profile_similarity(rec, profile, rec_title_tokens)
            if score > best_score:
                best_score = score
                best_i = idx
        if best_i >= 0 and best_score >= settings.event_cluster_min_similarity:
            clusters[best_i].append(rec)
            profiles[best_i].update(rec)
        else:
            clusters.append([rec])
            profiles.append(_ClusterProfile.from_record(rec))

    result: list[list[dict[str, Any]]] = []
    for cluster in clusters:
        cluster.sort(key=lambda r: (r["views"], r["date"] or datetime.min), reverse=True)
        result.append([r["row"] for r in cluster])
    return result


def _event_key_from_cluster(rows: list[dict[str, Any]]) -> str:
    records = [_news_record(r) for r in rows]
    token_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    dates = [r["date"] for r in records if isinstance(r.get("date"), datetime)]
    for r in records:
        token_counter.update(r["token_list"][:35])
        tag_counter.update([t.casefold().replace("ё", "е") for t in r["tags"][:15]])
    top_tokens = [w for w, _ in token_counter.most_common(8)]
    top_tags = [w for w, _ in tag_counter.most_common(6)]
    if dates:
        day_bucket = str(min(d.date().toordinal() for d in dates) // max(1, settings.event_cluster_window_days))
    else:
        day_bucket = "unknown"
    raw = "|".join(sorted(top_tokens + top_tags)[:12]) + "|" + day_bucket
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _best_title(records: list[dict[str, Any]]) -> str:
    def score(rec: dict[str, Any]) -> float:
        title = rec["title"]
        if not title:
            return -1000
        length_score = 1.0 if 30 <= len(title) <= 140 else 0.2
        link_penalty = -2 if title.startswith(("http://", "https://", "www.")) else 0
        return rec["views"] * 0.002 + length_score + len(rec["source"] or "") * 0.01 + link_penalty
    best = max(records, key=score)
    return best["title"] or "Событие без заголовка"


def _split_sentences(text: str) -> list[str]:
    parts = []
    for part in _SENT_RE.split(text or ""):
        part = _clean_text(part, 360)
        if len(part) < 45:
            continue
        if part.startswith(("http://", "https://", "www.")):
            continue
        parts.append(part)
    return parts


def _extractive_summary(records: list[dict[str, Any]], top_tokens: list[str]) -> str:
    keyset = set(top_tokens[:25])
    candidates: list[tuple[float, str]] = []
    for rec in records[: settings.event_context_sources_limit]:
        for sentence in _split_sentences(rec["text"]):
            sent_tokens = set(_tokens(sentence, 60))
            if not sent_tokens:
                continue
            overlap = len(sent_tokens & keyset)
            length = len(sentence)
            length_bonus = 1.0 if 90 <= length <= 260 else 0.35
            source_bonus = 0.25 if rec.get("source") else 0
            views_bonus = min(1.0, math.log1p(rec.get("views") or 0) / 8)
            score = overlap * 2.0 + length_bonus + source_bonus + views_bonus
            candidates.append((score, sentence))
    candidates.sort(key=lambda x: x[0], reverse=True)
    selected: list[str] = []
    selected_tokens: list[set[str]] = []
    for _, sent in candidates:
        toks = set(_tokens(sent, 60))
        if any(_jaccard(toks, prev) > 0.62 for prev in selected_tokens):
            continue
        selected.append(sent)
        selected_tokens.append(toks)
        if len(selected) >= 3:
            break
    if selected:
        return _clean_text(" ".join(selected), 900)
    # fallback: несколько лучших фрагментов
    snippets = [_clean_text(r["text"], 260) for r in records[:2] if r["text"]]
    return _clean_text(" ".join(snippets), 900)


def _facet_from_records(records: list[dict[str, Any]], key: str, limit: int) -> list[str]:
    counter: Counter[str] = Counter()
    for rec in records:
        counter.update(rec.get(key) or [])
    return [x for x, _ in counter.most_common(limit)]


def _sigma(records: list[dict[str, Any]]) -> int:
    sources = {r.get("source") or str(r.get("id")) for r in records}
    score = 52
    score += min(24, len(records) * 3)
    score += min(18, len(sources) * 5)
    if len(sources) >= 2:
        score += 4
    if len(records) >= 4:
        score += 3
    return max(50, min(96, score))


async def analyze_group_offline_rag(event_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Локальный RAG-like анализ без платных LLM.

    Retrieval уже случился на этапе cluster_news_rows(): группа содержит только источники,
    похожие по токенам, тегам и датам. Здесь мы строим карточку события extractive способом:
    выбираем лучший заголовок, извлекаем важные предложения из источников и добавляем
    rule-based impact по ролям.
    """
    records = [_news_record(r) for r in rows]
    token_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    for rec in records:
        token_counter.update(rec["token_list"][:50])
        tag_counter.update(rec["tags"][:30])

    tags = [tag for tag, _ in tag_counter.most_common(30)]
    top_tokens = [w for w, _ in token_counter.most_common(40)]
    title = _best_title(records)
    summary = _extractive_summary(records, top_tokens)
    topics = _facet_from_records(records, "topics", 20) or tags[:10]
    regions = _facet_from_records(records, "regions", 20)
    products = _facet_from_records(records, "products", 20)
    impacts = _role_impacts(tags + topics + regions + products, f"{title} {summary}")

    context_sources = []
    for rec in records[: settings.event_context_sources_limit]:
        context_sources.append(
            {
                "id": rec["id"],
                "date": rec["date"].isoformat() if isinstance(rec.get("date"), datetime) else None,
                "title": rec["title"],
                "source": rec.get("source"),
                "snippet": _clean_text(rec.get("text"), 500),
                "tags": rec.get("tags", [])[:16],
            }
        )

    return {
        "title": title,
        "summary": summary,
        "tags": tags,
        "topics": topics,
        "regions": regions,
        "products": products,
        "impacts": impacts,
        "sigma": _sigma(records),
        "raw_llm": {
            "offline_rag": True,
            "paid_llm_used": False,
            "analysis_mode": settings.event_analysis_mode,
            "retrieval": "local token/tag/date clustering",
            "summary": "extractive",
            "evidence_tokens": top_tokens[:24],
            "context_sources": context_sources,
        },
    }


# Оставляем старое имя, чтобы не ломать импорты/старые вызовы.
async def analyze_group_with_llm(event_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return await analyze_group_offline_rag(event_id, rows)


async def upsert_event_from_group(event_key: str, rows: list[dict[str, Any]], analysis: dict[str, Any]) -> int:
    await ensure_event_schema()
    date_by_news_id = {int(r["id"]): r.get("date") for r in rows if r.get("id") is not None}
    news_items = [row_to_news(r) for r in rows]
    news_items.sort(key=lambda n: ((n.get("views") or 0), n.get("date") or ""), reverse=True)
    main = news_items[0]
    dates = [r.get("date") for r in rows if isinstance(r.get("date"), datetime)]
    source_names = {(n.get("source") or n.get("customer") or "источник").strip() for n in news_items}
    source_count = len([x for x in source_names if x]) or len(news_items)
    event_status = "active" if source_count >= settings.event_min_sources else "ignored_weak"
    views = sum(int(n.get("views") or 0) for n in news_items)

    params = {
        "event_key": event_key,
        "title": analysis.get("title") or main.get("title") or "Событие без заголовка",
        "summary": analysis.get("summary") or main.get("summary") or "",
        "status": event_status,
        "sigma": int(analysis.get("sigma") or 60),
        "news_count": len(news_items),
        "sources_count": source_count,
        "views": views,
        "date_from": min(dates) if dates else None,
        "date_to": max(dates) if dates else None,
        "main_news_id": int(main["id"]),
        "tags": Jsonb(_as_str_list(analysis.get("tags"), 30)),
        "topics": Jsonb(_as_str_list(analysis.get("topics"), 20)),
        "regions": Jsonb(_as_str_list(analysis.get("regions"), 20)),
        "products": Jsonb(_as_str_list(analysis.get("products"), 20)),
        "raw_llm": Jsonb(analysis.get("raw_llm") or {}),
        "model": settings.event_analysis_mode,
    }

    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {events} (
                        event_key, title, summary, status, sigma, news_count, sources_count, views,
                        date_from, date_to, main_news_id, tags, topics, regions, products,
                        raw_llm, model, processed_at, last_seen_at
                    ) VALUES (
                        %(event_key)s, %(title)s, %(summary)s, %(status)s, %(sigma)s, %(news_count)s,
                        %(sources_count)s, %(views)s, %(date_from)s, %(date_to)s,
                        %(main_news_id)s, %(tags)s, %(topics)s, %(regions)s, %(products)s,
                        %(raw_llm)s, %(model)s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (event_key) DO UPDATE SET
                        title = EXCLUDED.title,
                        summary = EXCLUDED.summary,
                        status = EXCLUDED.status,
                        sigma = EXCLUDED.sigma,
                        tags = EXCLUDED.tags,
                        topics = EXCLUDED.topics,
                        regions = EXCLUDED.regions,
                        products = EXCLUDED.products,
                        raw_llm = EXCLUDED.raw_llm,
                        model = EXCLUDED.model,
                        processed_at = CURRENT_TIMESTAMP,
                        last_seen_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id
                    """
                ).format(events=event_table_identifier("events")),
                params,
            )
            row = await cur.fetchone()
            event_id = int(row["id"])

            await cur.executemany(
                sql.SQL(
                    """
                    INSERT INTO {sources} (
                        event_id, news_id, news_date, title, source, customer, link_site, snippet, views
                    ) VALUES (
                        %(event_id)s, %(news_id)s, %(news_date)s, %(title)s, %(source)s,
                        %(customer)s, %(link_site)s, %(snippet)s, %(views)s
                    )
                    ON CONFLICT (event_id, news_id) DO UPDATE SET
                        news_date = EXCLUDED.news_date,
                        title = EXCLUDED.title,
                        source = EXCLUDED.source,
                        customer = EXCLUDED.customer,
                        link_site = EXCLUDED.link_site,
                        snippet = EXCLUDED.snippet,
                        views = EXCLUDED.views
                    """
                ).format(sources=event_table_identifier("event_sources")),
                [
                    {
                        "event_id": event_id,
                        "news_id": int(n["id"]),
                        "news_date": date_by_news_id.get(int(n["id"])),
                        "title": n.get("title"),
                        "source": n.get("source"),
                        "customer": n.get("customer"),
                        "link_site": n.get("link_site"),
                        "snippet": _clean_text(n.get("text") or n.get("summary"), 500),
                        "views": int(n.get("views") or 0),
                    }
                    for n in news_items
                ],
            )

            await cur.execute(
                sql.SQL("DELETE FROM {impacts} WHERE event_id = %(event_id)s").format(
                    impacts=event_table_identifier("event_impacts")
                ),
                {"event_id": event_id},
            )
            impacts = analysis.get("impacts") or _role_impacts(_as_str_list(analysis.get("tags")), params["title"])
            for imp in impacts:
                normalized = _normalize_impact(imp) if isinstance(imp, dict) else None
                if not normalized:
                    continue
                await cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {impacts} (event_id, role, label, impact, summary, action_hint, updated_at)
                        VALUES (%(event_id)s, %(role)s, %(label)s, %(impact)s, %(summary)s, %(action_hint)s, CURRENT_TIMESTAMP)
                        ON CONFLICT (event_id, role) DO UPDATE SET
                            label = EXCLUDED.label,
                            impact = EXCLUDED.impact,
                            summary = EXCLUDED.summary,
                            action_hint = EXCLUDED.action_hint,
                            updated_at = CURRENT_TIMESTAMP
                        """
                    ).format(impacts=event_table_identifier("event_impacts")),
                    {"event_id": event_id, **normalized},
                )

            # Пересчитываем агрегаты уже по всем источникам события.
            await cur.execute(
                sql.SQL(
                    """
                    UPDATE {events} e
                    SET news_count = agg.news_count,
                        sources_count = agg.sources_count,
                        status = CASE
                            WHEN agg.sources_count >= %(event_min_sources)s THEN 'active'
                            ELSE 'ignored_weak'
                        END,
                        views = agg.views,
                        date_from = agg.date_from,
                        date_to = agg.date_to,
                        updated_at = CURRENT_TIMESTAMP
                    FROM (
                        SELECT event_id,
                               COUNT(*)::int AS news_count,
                               COUNT(DISTINCT COALESCE(NULLIF(source, ''), NULLIF(customer, ''), news_id::text))::int AS sources_count,
                               COALESCE(SUM(views), 0)::int AS views,
                               MIN(news_date) AS date_from,
                               MAX(news_date) AS date_to
                        FROM {sources}
                        WHERE event_id = %(event_id)s
                        GROUP BY event_id
                    ) agg
                    WHERE e.id = agg.event_id
                    """
                ).format(events=event_table_identifier("events"), sources=event_table_identifier("event_sources")),
                {"event_id": event_id, "event_min_sources": settings.event_min_sources},
            )
        await conn.commit()
    cache_delete_prefix("events")
    return event_id


async def _latest_news_date_for_worker(news_table: sql.Composable) -> date:
    """Последняя дата в news_list для worker-а.

    Используем дату данных, а не системную дату сервера. Если в таблицу загружены
    новости с датой позже текущего дня или исторический срез, worker всё равно
    обработает актуальный хвост выгрузки.
    """
    value = await fetch_val(
        sql.SQL(
            "SELECT MAX(n.date)::date FROM {news_table} n WHERE {quality}"
        ).format(news_table=news_table, quality=QUALITY_WHERE),
        {"event_excluded_title_patterns": _EVENT_EXCLUDED_TITLE_PATTERNS},
    )
    return value if isinstance(value, date) else date.today()


async def fetch_unprocessed_news() -> list[dict[str, Any]]:
    await ensure_event_schema()
    news_table = await news_table_identifier()
    params: dict[str, Any] = {
        "limit": settings.event_worker_fetch_limit,
        "event_excluded_title_patterns": _EVENT_EXCLUDED_TITLE_PATTERNS,
    }
    date_filter = sql.SQL("")
    if not settings.event_worker_process_all:
        anchor_date = await _latest_news_date_for_worker(news_table)
        params["date_from"] = datetime.combine(anchor_date - timedelta(days=settings.event_worker_lookback_days), time.min)
        date_filter = sql.SQL(" AND n.date >= %(date_from)s")

    query = sql.SQL(
        """
        SELECT {columns}
        FROM {news_table} n
        WHERE {quality}
          {date_filter}
          AND NOT (LOWER(COALESCE(n.title, '')) LIKE ANY(%(event_excluded_title_patterns)s))
          AND NOT EXISTS (
              SELECT 1 FROM {sources} es WHERE es.news_id = n.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM {state} st WHERE st.news_id = n.id
          )
        ORDER BY n.date DESC NULLS LAST, n.id DESC
        LIMIT %(limit)s
        """
    ).format(
        columns=NEWS_COLUMNS,
        news_table=news_table,
        quality=QUALITY_WHERE,
        date_filter=date_filter,
        sources=event_table_identifier("event_sources"),
        state=_event_news_state_table(),
    )
    return await fetch_all(query, params)


async def mark_event_news_batch(rows: list[dict[str, Any]], clustered_news_ids: set[int]) -> dict[str, int]:
    """Помечает все новости batch-а как просмотренные event-worker-ом.

    Использует executemany для отправки всех строк одним батчем вместо N отдельных
    round-trip к базе данных.
    """
    if not rows:
        return {"seen": 0, "clustered": 0, "skipped": 0}

    batch: list[dict[str, Any]] = []
    clustered = 0
    skipped = 0

    for r in rows:
        try:
            news_id = int(r.get("id"))
        except Exception:
            continue
        is_clustered = news_id in clustered_news_ids
        batch.append({
            "news_id": news_id,
            "news_date": r.get("date") if isinstance(r.get("date"), datetime) else None,
            "status": "clustered" if is_clustered else "skipped",
            "reason": "event_source_written" if is_clustered else "not_used_by_semantic_cluster",
        })
        if is_clustered:
            clustered += 1
        else:
            skipped += 1

    if not batch:
        return {"seen": 0, "clustered": 0, "skipped": 0}

    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                sql.SQL(
                    """
                    INSERT INTO {state} (news_id, news_date, status, reason, processed_at, updated_at)
                    VALUES (%(news_id)s, %(news_date)s, %(status)s, %(reason)s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (news_id) DO UPDATE SET
                        news_date = EXCLUDED.news_date,
                        status = EXCLUDED.status,
                        reason = EXCLUDED.reason,
                        processed_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ).format(state=_event_news_state_table()),
                batch,
            )
        await conn.commit()

    return {"seen": len(batch), "clustered": clustered, "skipped": skipped}


async def prune_stale_inactive_events(days: int | None = None) -> dict[str, Any]:
    """Удаляет неактивные (ignored_weak) события старше N дней, чтобы не копить мусор.

    Отсчёт ведём от самой свежей даты события в БД (а не от системного времени) —
    устойчиво и к live-потоку, и к историческому срезу. Окно склейки = 5 дней, так что
    событие старше порога уже не станет активным. По FK каскадно удаляются его
    event_sources / event_impacts / event_links / event_graph_rows. Новости остаются
    в event_news_state, поэтому повторно не обрабатываются.
    """
    await ensure_event_schema()
    n = settings.event_prune_inactive_days if days is None else days
    events_t = event_table_identifier("events")
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                sql.SQL(
                    """
                    DELETE FROM {events}
                    WHERE status <> 'active'
                      AND date_to IS NOT NULL
                      AND date_to < (SELECT MAX(date_to) FROM {events}) - make_interval(days => %(days)s)
                    """
                ).format(events=events_t),
                {"days": int(n)},
            )
            deleted = cur.rowcount
        await conn.commit()
    if deleted:
        cache_delete_prefix("events")
    logger.info("pruned %s stale inactive events (older than %s days)", deleted, n)
    return {"deleted": int(deleted or 0), "days": int(n)}


async def process_events_once() -> dict[str, Any]:
    await ensure_event_schema()
    rows = await fetch_unprocessed_news()
    if not rows:
        return {"status": "ok", "fetched": 0, "groups": 0, "events_upserted": 0, "mode": settings.event_analysis_mode}

    # Ограничиваем batch, чтобы worker не держал БД слишком долго.
    rows = rows[: settings.event_worker_batch_size]
    mode = (settings.event_analysis_mode or "offline-rag").lower()
    semantic_used = False
    if mode in {"semantic-rag", "semantic", "local-semantic-rag"}:
        try:
            from app.services.semantic_rag import analyze_group_semantic_rag, cluster_news_rows_semantic, semantic_available

            if semantic_available():
                clusters = cluster_news_rows_semantic(rows)
                analyzer = analyze_group_semantic_rag
                semantic_used = True
            else:
                clusters = cluster_news_rows(rows)
                analyzer = analyze_group_offline_rag
        except Exception as exc:
            logger.exception("semantic-rag failed before processing, fallback to offline-rag: %s", exc)
            clusters = cluster_news_rows(rows)
            analyzer = analyze_group_offline_rag
    else:
        clusters = cluster_news_rows(rows)
        analyzer = analyze_group_offline_rag

    upserted = 0
    clustered_news_ids: set[int] = set()
    for group_rows in clusters:
        if not group_rows:
            continue
        event_key = _event_key_from_cluster(group_rows)
        analysis = await analyzer(event_key, group_rows)
        await upsert_event_from_group(event_key, group_rows, analysis)
        for r in group_rows:
            if r.get("id") is not None:
                clustered_news_ids.add(int(r["id"]))
        upserted += 1

    batch_state = await mark_event_news_batch(rows, clustered_news_ids)

    return {
        "status": "ok",
        "fetched": len(rows),
        "groups": len(clusters),
        "events_upserted": upserted,
        "news_seen": batch_state.get("seen", 0),
        "news_clustered": batch_state.get("clustered", 0),
        "news_skipped": batch_state.get("skipped", 0),
        "mode": "semantic-rag" if semantic_used else settings.event_analysis_mode,
        "semantic_used": semantic_used,
    }


async def _event_filters(
    *,
    q: str | None = None,
    topic: list[str] | None = None,
    tag: list[str] | None = None,
    region: str | None = None,
    product: str | None = None,
    source: str | None = None,
    period: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    relaxed_period_only: bool = False,
) -> tuple[sql.Composed, dict[str, Any]]:
    """Фильтры для событий.

    При клике по графику важнее не потерять события периода. Поэтому дата
    матчится несколькими способами: по агрегированному диапазону события,
    по event_sources.news_date и, для старых/частично пересобранных событий,
    через join к news_list по source news_id/main_news_id.

    relaxed_period_only=True используется как fallback: если строгие фильтры
    (тема/регион/продукт/источник) не нашли событий, показываем события самого
    периода, чтобы UI не писал "нет событий", когда они физически есть в таблице.
    """
    where: list[sql.Composable] = [sql.SQL("e.status = 'active'"), sql.SQL("e.sources_count >= %(event_min_sources)s")]
    params: dict[str, Any] = {"event_min_sources": settings.event_min_sources}

    if q:
        params["q_like"] = f"%{q.lower()}%"
        where.append(sql.SQL("(LOWER(e.title) LIKE %(q_like)s OR LOWER(e.summary) LIKE %(q_like)s)"))

    if not relaxed_period_only:
        # Основной слой событий — topics. Старый параметр tag оставляем как
        # обратную совместимость и трактуем его как topic.
        # Дополнительно смотрим не только e.topics, но и нормализованный индекс
        # тем у новостей-источников события. Это закрывает старые события,
        # собранные до перехода на topics/news_topic_marks.
        effective_topics: list[str] = []
        for value in [*(topic or []), *(tag or [])]:
            if value and value not in effective_topics:
                effective_topics.append(value)

        if effective_topics:
            topic_norm_values = sorted({_topic_norm(v) for v in effective_topics if _topic_norm(v)})
            params["topic_values"] = effective_topics
            params["event_topic_norm_values"] = topic_norm_values
            params["event_topic_norm_count"] = len(topic_norm_values)
            if topic_norm_values:
                where.append(
                    sql.SQL(
                        """
                        (
                            (CASE WHEN jsonb_typeof(e.topics) IN ('array','object') THEN e.topics ELSE '[]'::jsonb END) ?& %(topic_values)s
                            OR e.id IN (
                                SELECT src.event_id
                                FROM {sources} src
                                JOIN {marks} nt ON nt.news_id = src.news_id
                                WHERE nt.topic_norm = ANY(%(event_topic_norm_values)s)
                                GROUP BY src.event_id
                                HAVING COUNT(DISTINCT nt.topic_norm) = %(event_topic_norm_count)s
                            )
                        )
                        """
                    ).format(sources=event_table_identifier("event_sources"), marks=_topic_marks_table())
                )

        if region:
            params["region"] = region
            where.append(sql.SQL("(CASE WHEN jsonb_typeof(e.regions) = 'array' THEN e.regions ELSE '[]'::jsonb END) ? %(region)s"))
        if product:
            params["product"] = product
            where.append(sql.SQL("(CASE WHEN jsonb_typeof(e.products) = 'array' THEN e.products ELSE '[]'::jsonb END) ? %(product)s"))
        if source:
            params["source"] = source.lower()
            where.append(
                sql.SQL(
                    "EXISTS (SELECT 1 FROM {sources} src WHERE src.event_id = e.id AND LOWER(COALESCE(src.source, src.customer, '')) = %(source)s)"
                ).format(sources=event_table_identifier("event_sources"))
            )

    if date_from and date_to:
        if date_to < date_from:
            date_from, date_to = date_to, date_from
        params["date_from"] = datetime.combine(date_from, time.min)
        params["date_to"] = datetime.combine(date_to + timedelta(days=1), time.min)
        news_table = await news_table_identifier()
        where.append(
            sql.SQL(
                """
                (
                    (e.date_from IS NOT NULL AND e.date_to IS NOT NULL AND e.date_to >= %(date_from)s AND e.date_from < %(date_to)s)
                    OR (e.date_from IS NOT NULL AND e.date_to IS NULL AND e.date_from >= %(date_from)s AND e.date_from < %(date_to)s)
                    OR (e.date_to IS NOT NULL AND e.date_from IS NULL AND e.date_to >= %(date_from)s AND e.date_to < %(date_to)s)
                    OR EXISTS (
                        SELECT 1
                        FROM {sources} src
                        WHERE src.event_id = e.id
                          AND src.news_date >= %(date_from)s
                          AND src.news_date < %(date_to)s
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM {sources} src
                        JOIN {news_table} sn ON sn.id = src.news_id
                        WHERE src.event_id = e.id
                          AND sn.date >= %(date_from)s
                          AND sn.date < %(date_to)s
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM {news_table} mn
                        WHERE mn.id = e.main_news_id
                          AND mn.date >= %(date_from)s
                          AND mn.date < %(date_to)s
                    )
                )
                """
            ).format(sources=event_table_identifier("event_sources"), news_table=news_table)
        )
    else:
        # Только ручной period из фильтров. Переключение масштаба графика сюда не попадает.
        _, params_news = _build_filters(period=period)
        if "date_from" in params_news and "date_to" in params_news:
            params["date_from"] = params_news["date_from"]
            params["date_to"] = params_news["date_to"]
            where.append(sql.SQL("e.date_to >= %(date_from)s AND e.date_from < %(date_to)s"))

    return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where), params

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
    sort: str | None = None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    cache_key = (
        "events_topics_v8_min_sources", settings.event_min_sources, q or "", frozen_list(topic), frozen_list(tag), region or "", product or "",
        source or "", period or "", str(date_from or ""), str(date_to or ""), role or "", sort or "", limit, offset,
    )
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    await ensure_event_schema()
    where, params = await _event_filters(q=q, topic=topic, tag=tag, region=region, product=product, source=source, period=period, date_from=date_from, date_to=date_to)
    params.update({"limit": limit, "offset": offset})

    role_join = sql.SQL("")
    role_score = sql.SQL("0")
    if role:
        params["role"] = role
        role_join = sql.SQL("LEFT JOIN {impacts} ri ON ri.event_id = e.id AND ri.role = %(role)s").format(
            impacts=event_table_identifier("event_impacts")
        )
        role_score = sql.SQL("CASE ri.impact WHEN 'positive' THEN 3 WHEN 'negative' THEN 3 WHEN 'watch' THEN 2 ELSE 0 END")

    # sort='date_desc' → самые свежие события первыми (для главной).
    # По умолчанию — ранжирование по значимости (роль/sigma/источники).
    if sort == "date_desc":
        order_by = sql.SQL("ORDER BY e.date_to DESC NULLS LAST, e.date_from DESC NULLS LAST, e.id DESC")
    else:
        order_by = sql.SQL("ORDER BY role_score DESC, e.sigma DESC, e.sources_count DESC, e.news_count DESC, e.date_to DESC NULLS LAST, e.id DESC")

    async def _run_query(local_where: sql.Composed, local_params: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
        total_query = sql.SQL("SELECT COUNT(*) FROM {events} e {where}").format(
            events=event_table_identifier("events"), where=local_where
        )
        local_total = int(await fetch_val(total_query, local_params) or 0)

        query = sql.SQL(
            """
            SELECT e.id, e.event_key, e.title, e.summary, e.date_from, e.date_to,
                   e.news_count, e.sources_count, e.sigma, e.views,
                   e.tags, e.topics, e.regions, e.products, e.main_news_id,
                   {role_score} AS role_score
            FROM {events} e
            {role_join}
            {where}
            {order_by}
            LIMIT %(limit)s OFFSET %(offset)s
            """
        ).format(
            events=event_table_identifier("events"),
            role_join=role_join,
            where=local_where,
            role_score=role_score,
            order_by=order_by,
        )
        local_rows = await fetch_all(query, local_params)
        return local_total, local_rows

    total, rows = await _run_query(where, params)
    fallback_applied = False

    # Если пользователь кликнул по столбцу графика, событие должно находиться
    # в первую очередь по периоду. Старые события могли быть собраны с другими
    # topics/regions/products, поэтому при нуле строк делаем мягкий fallback:
    # показываем события выбранного периода без дополнительных facets-фильтров.
    if (
        not rows
        and date_from
        and date_to
        and offset == 0
        and (topic or tag or region or product or source)
    ):
        fallback_where, fallback_params = await _event_filters(
            q=q,
            topic=[],
            tag=[],
            region=None,
            product=None,
            source=None,
            period=None,
            date_from=date_from,
            date_to=date_to,
            relaxed_period_only=True,
        )
        fallback_params.update({"limit": limit, "offset": offset})
        if role:
            fallback_params["role"] = role
        fallback_total, fallback_rows = await _run_query(fallback_where, fallback_params)
        if fallback_rows:
            total, rows, params = fallback_total, fallback_rows, fallback_params
            fallback_applied = True

    if not rows:
        return cache_set(cache_key, {"total": total, "limit": limit, "offset": offset, "items": [], "period_fallback": fallback_applied}, 120)

    ids = [int(r["id"]) for r in rows]
    sources_rows = await fetch_all(
        sql.SQL(
            """
            SELECT event_id, news_id, title, source, customer, news_date, link_site
            FROM {sources}
            WHERE event_id = ANY(%(ids)s)
            ORDER BY event_id, news_date DESC NULLS LAST, news_id DESC
            """
        ).format(sources=event_table_identifier("event_sources")),
        {"ids": ids},
    )
    impacts_rows = await fetch_all(
        sql.SQL(
            """
            SELECT event_id, role, label, impact, summary, action_hint
            FROM {impacts}
            WHERE event_id = ANY(%(ids)s)
            ORDER BY event_id, role
            """
        ).format(impacts=event_table_identifier("event_impacts")),
        {"ids": ids},
    )

    sources_by_event: dict[int, list[dict[str, Any]]] = {}
    for src in sources_rows:
        eid = int(src["event_id"])
        if len(sources_by_event.setdefault(eid, [])) >= 6:
            continue
        d = src.get("news_date")
        sources_by_event[eid].append(
            {
                "id": int(src["news_id"]),
                "title": src.get("title") or "Без заголовка",
                "source": src.get("source") or src.get("customer"),
                "date": d.isoformat() if isinstance(d, datetime) else None,
                "link_site": src.get("link_site"),
            }
        )

    impacts_by_event: dict[int, list[dict[str, Any]]] = {}
    for imp in impacts_rows:
        eid = int(imp["event_id"])
        impacts_by_event.setdefault(eid, []).append(
            {
                "role": imp.get("role"),
                "label": imp.get("label") or _ROLE_LABELS.get(str(imp.get("role")), str(imp.get("role"))),
                "impact": imp.get("impact") or "watch",
                "summary": imp.get("summary") or "",
                "action_hint": imp.get("action_hint") or "",
            }
        )

    items = []
    for r in rows:
        eid = int(r["id"])
        date_from_value = r.get("date_from")
        date_to_value = r.get("date_to")
        items.append(
            {
                "id": str(eid),
                "title": r.get("title") or "Событие без заголовка",
                "summary": r.get("summary") or "",
                "date_from": date_from_value.date().isoformat() if isinstance(date_from_value, datetime) else None,
                "date_to": date_to_value.date().isoformat() if isinstance(date_to_value, datetime) else None,
                "news_count": int(r.get("news_count") or 0),
                "sources_count": int(r.get("sources_count") or 0),
                "sigma": int(r.get("sigma") or 50),
                "views": int(r.get("views") or 0),
                "tags": _as_str_list(r.get("tags"), 30),
                "topics": _as_str_list(r.get("topics"), 20),
                "regions": _as_str_list(r.get("regions"), 20),
                "products": _as_str_list(r.get("products"), 20),
                "impacts": impacts_by_event.get(eid, []),
                "sources": sources_by_event.get(eid, []),
                "main_news_id": r.get("main_news_id"),
            }
        )

    return cache_set(cache_key, {"total": total, "limit": limit, "offset": offset, "items": items, "period_fallback": fallback_applied}, 120)


async def event_sources(event_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """Список источников одного события (для выпадашки на странице чтения)."""
    cache_key = ("event_sources_v1", int(event_id), int(limit))
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    await ensure_event_schema()
    rows = await fetch_all(
        sql.SQL(
            """
            SELECT news_id, title, source, customer, news_date, link_site
            FROM {sources}
            WHERE event_id = %(eid)s
            ORDER BY news_date DESC NULLS LAST, news_id DESC
            LIMIT %(limit)s
            """
        ).format(sources=event_table_identifier("event_sources")),
        {"eid": int(event_id), "limit": int(limit)},
    )
    items = [
        {
            "id": int(r["news_id"]),
            "title": r.get("title") or "Без заголовка",
            "source": r.get("source") or r.get("customer"),
            "date": r["news_date"].isoformat() if isinstance(r.get("news_date"), datetime) else None,
            "link_site": r.get("link_site"),
        }
        for r in rows
    ]
    return cache_set(cache_key, items, 120)


async def event_detail(event_id: int) -> dict[str, Any]:
    """Источники + impacts по ролям одного события (для шапки страницы чтения)."""
    cache_key = ("event_detail_v1", int(event_id))
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    await ensure_event_schema()
    srcs = await event_sources(event_id)
    impact_rows = await fetch_all(
        sql.SQL(
            """
            SELECT role, label, impact, summary, action_hint
            FROM {impacts}
            WHERE event_id = %(eid)s
            ORDER BY role
            """
        ).format(impacts=event_table_identifier("event_impacts")),
        {"eid": int(event_id)},
    )
    impacts = [
        {
            "role": r.get("role"),
            "label": r.get("label") or _ROLE_LABELS.get(str(r.get("role")), str(r.get("role"))),
            "impact": r.get("impact") or "watch",
            "summary": r.get("summary") or "",
            "action_hint": r.get("action_hint") or "",
        }
        for r in impact_rows
    ]
    return cache_set(cache_key, {"sources": srcs, "impacts": impacts}, 120)


async def events_stats() -> dict[str, Any]:
    cache_key = ("events_stats",)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    await ensure_event_schema()
    row = await fetch_one(
        sql.SQL(
            """
            SELECT COUNT(*)::int AS events,
                   COALESCE(SUM(news_count), 0)::int AS linked_news,
                   MAX(processed_at) AS last_processed_at
            FROM {events}
            WHERE status = 'active' AND sources_count >= %(event_min_sources)s
            """
        ).format(events=event_table_identifier("events")),
        {"event_min_sources": settings.event_min_sources},
    )
    state = await fetch_one(
        sql.SQL("SELECT value, updated_at FROM {state} WHERE key = 'events_worker_last_run'").format(
            state=event_table_identifier("event_job_state")
        )
    )
    return cache_set(cache_key, {"stats": row or {}, "worker_state": state or None, "schema": settings.events_schema}, 60)


async def list_events_graph(
    *,
    q: str | None = None,
    topic: list[str],
    tag: list[str],
    region: str | None = None,
    product: str | None = None,
    source: str | None = None,
    period: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Лёгкий датасет для SVG-графа: только id/date/topics/regions/products, без sources/impacts/summary."""
    cache_key = (
        "events_graph_v1", settings.event_min_sources, q or "",
        frozen_list(topic), frozen_list(tag), region or "", product or "",
        source or "", period or "", str(date_from or ""), str(date_to or ""), limit,
    )
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    await ensure_event_schema()

    # Быстрый путь: график на главной идёт без фильтров → читаем предрасчётную
    # проекцию event_graph_rows (грани уже как TEXT[], без JSONB-парсинга на лету).
    # При фильтрах или пустой проекции (первый деплой) — обычный путь по events ниже.
    no_filters = not (q or topic or tag or region or product or source or period or date_from or date_to)
    if no_filters:
        egr_rows = await fetch_all(
            sql.SQL(
                """
                SELECT event_id, date_from, date_to, topics, regions, products
                FROM {egr}
                ORDER BY sigma DESC, sources_count DESC, date_to DESC NULLS LAST
                LIMIT %(limit)s
                """
            ).format(egr=event_table_identifier("event_graph_rows")),
            {"limit": limit},
        )
        if egr_rows:
            items = [
                {
                    "id": str(int(r["event_id"])),
                    "date_from": r["date_from"].isoformat() if r.get("date_from") else None,
                    "date_to": r["date_to"].isoformat() if r.get("date_to") else None,
                    "topics": list(r.get("topics") or []),
                    "regions": list(r.get("regions") or []),
                    "products": list(r.get("products") or []),
                }
                for r in egr_rows
            ]
            return cache_set(cache_key, {"total": len(items), "items": items}, 60)
        # проекция ещё не построена → падаем на обычный путь по events

    where, params = await _event_filters(
        q=q, topic=topic, tag=tag, region=region, product=product,
        source=source, period=period, date_from=date_from, date_to=date_to,
    )
    params["limit"] = limit

    query = sql.SQL(
        """
        SELECT e.id, e.date_from, e.date_to, e.topics, e.regions, e.products
        FROM {events} e
        {where}
        ORDER BY e.sigma DESC, e.sources_count DESC, e.date_to DESC NULLS LAST
        LIMIT %(limit)s
        """
    ).format(events=event_table_identifier("events"), where=where)

    rows = await fetch_all(query, params)
    items = []
    for r in rows:
        df = r.get("date_from")
        dt = r.get("date_to")
        items.append({
            "id": str(int(r["id"])),
            "date_from": df.date().isoformat() if isinstance(df, datetime) else None,
            "date_to": dt.date().isoformat() if isinstance(dt, datetime) else None,
            "topics": _as_str_list(r.get("topics"), 20),
            "regions": _as_str_list(r.get("regions"), 20),
            "products": _as_str_list(r.get("products"), 20),
        })

    return cache_set(cache_key, {"total": len(items), "items": items}, 60)


# Известные «игроки» (компании, ведомства, персоны) — выделяются из topics в отдельный
# канал «игрок» (A) для explorer-таймлайна, как в примере harvester_tree.html.
_ACTOR_NAMES = frozenset({
    "Cofco", "Абрамченко", "Агроэкспорт", "Акрон", "Астон", "Гап «Ресурс»", "Гордеев", "Данкверт",
    "Двойных", "Еврохим", "Зерновой Союз", "Каргилл", "Кашин", "Комос Групп", "Кондратьев", "Лут",
    "Макфа", "Масложировой Союз", "Мираторг", "Мишустин", "Нса", "Патрушев", "Продимекс", "Разин",
    "Рзс", "Родные Поля", "Россельхозбанк", "Россельхознадзор", "Росспецмаш", "Росстат", "Ростсельмаш",
    "Русагро", "Русагротранс", "Совэкон", "Томенко", "Уралхим", "Фосагро", "Фтс", "Черкизово",
    "Щёлково Агрохим", "Эконива", "Эфко", "Юг Руси", "Правительство", "Минсельхоз",
})


async def full_event_graph(focus_news_id: int | None = None) -> dict[str, Any]:
    """Весь активный граф событий (узлы + рёбра + сюжеты) для explorer на странице чтения.

    Аналог встроенного DATA в примере harvester_tree.html: клиент строит эго-граф,
    колесо недель и навигацию полностью на этих данных. Тяжёлая часть (граф) кэшируется;
    focus_event_id резолвится под конкретную новость (дёшево, без кэша)."""
    cache_key = ("full_event_graph_v1", settings.event_min_sources)
    result = cache_get(cache_key)
    if result is None:
        await ensure_event_schema()
        events_t = event_table_identifier("events")
        links_t = event_table_identifier("event_links")
        stories_t = event_table_identifier("event_stories")

        ev_rows = await fetch_all(
            sql.SQL(
                """
                SELECT id, date_from, title, summary, sigma, sources_count, main_news_id,
                       topics, regions, products, story_id, story_pos
                FROM {events}
                WHERE status = 'active' AND sources_count >= %(min_src)s AND date_from IS NOT NULL
                ORDER BY date_from, id
                """
            ).format(events=events_t),
            {"min_src": settings.event_min_sources},
        )
        nodes: list[dict[str, Any]] = []
        story_members: dict[int, list[tuple[int, int]]] = {}
        for r in ev_rows:
            eid = int(r["id"])
            df = r.get("date_from")
            topics = _as_str_list(r.get("topics"), 30)
            sid = r.get("story_id")
            if sid is not None:
                story_members.setdefault(int(sid), []).append((int(r.get("story_pos") or 0), eid))
            nodes.append({
                "id": eid,
                "date": df.date().isoformat() if isinstance(df, datetime) else None,
                "sg": int(r.get("sigma") or 0),
                "ti": _clean_text(r.get("title"), 200),
                "dek": _clean_text(r.get("summary"), 340),
                "src": int(r.get("sources_count") or 0),
                "main_news_id": r.get("main_news_id"),
                "p": _as_str_list(r.get("products"), 30),
                "g": _as_str_list(r.get("regions"), 30),
                "t": [x for x in topics if x not in _ACTOR_NAMES],
                "a": [x for x in topics if x in _ACTOR_NAMES],
                "s": [int(sid)] if sid is not None else [],
            })

        link_rows = await fetch_all(
            sql.SQL("SELECT from_id, to_id, weight, channel, lab FROM {links}").format(links=links_t)
        )
        edges = [
            [int(r["from_id"]), int(r["to_id"]), float(r.get("weight") or 0),
             r.get("channel") or "T", r.get("lab")]
            for r in link_rows
        ]

        story_rows = await fetch_all(
            sql.SQL("SELECT id, name, color FROM {stories}").format(stories=stories_t)
        )
        stories = [
            {
                "id": int(r["id"]),
                "name": r.get("name") or "Сюжет",
                "color": r.get("color") or "#6E5BD6",
                "ev": [eid for _pos, eid in sorted(story_members.get(int(r["id"]), []))],
            }
            for r in story_rows
        ]

        result = {"nodes": nodes, "edges": edges, "stories": stories}
        cache_set(cache_key, result, 120)

    focus_event_id = None
    if focus_news_id is not None:
        frow = await fetch_one(
            sql.SQL(
                """
                SELECT e.id FROM {events} e
                WHERE e.status = 'active'
                  AND ( e.main_news_id = %(nid)s
                        OR EXISTS (SELECT 1 FROM {sources} s WHERE s.event_id = e.id AND s.news_id = %(nid)s) )
                ORDER BY e.sigma DESC, e.sources_count DESC
                LIMIT 1
                """
            ).format(events=event_table_identifier("events"), sources=event_table_identifier("event_sources")),
            {"nid": int(focus_news_id)},
        )
        if frow:
            focus_event_id = int(frow["id"])

    # Обычная новость вне событий: показываем таймлайн ближайшего по теме события,
    # чтобы граф сюжетов был и под обычными новостями. Подбор — по пересечению
    # граней (продукт/регион/тема/игрок), при отсутствии пересечения — ближайшее по дате.
    if focus_event_id is None and focus_news_id is not None and result.get("nodes"):
        news_table = await news_table_identifier()
        nrow = await fetch_one(
            sql.SQL(
                "SELECT topics, regions, products, date FROM {news} n WHERE n.id = %(nid)s"
            ).format(news=news_table),
            {"nid": int(focus_news_id)},
        )
        if nrow:
            # news_list.{topics,regions,products} хранятся как JSON-объекты ({"Россия":"",...}),
            # поэтому парсим тем же json_to_list, что и в row_to_news (а не _as_str_list).
            n_facets = (
                set(json_to_list(nrow.get("topics")))
                | set(json_to_list(nrow.get("regions")))
                | set(json_to_list(nrow.get("products")))
            )
            best_id, best_score = None, 0
            for nd in result["nodes"]:
                node_facets = set(nd["t"]) | set(nd["a"]) | set(nd["g"]) | set(nd["p"])
                score = len(n_facets & node_facets)
                if score > best_score:
                    best_score, best_id = score, nd["id"]
            if best_id is None:
                # нет общих граней → ближайшее по дате (иначе — самое значимое)
                nd_date = nrow.get("date")
                target = nd_date.date() if isinstance(nd_date, datetime) else None
                if target is not None:
                    def _dist(nd: dict[str, Any]) -> int:
                        if not nd.get("date"):
                            return 10**9
                        return abs((date.fromisoformat(nd["date"]) - target).days)
                    best_id = min(result["nodes"], key=_dist)["id"]
                else:
                    best_id = max(result["nodes"], key=lambda nd: nd["sg"])["id"]
            focus_event_id = best_id

    return {**result, "focus_event_id": focus_event_id}


# ─── Story timeline (эго-граф сюжета вокруг события) ───────────────────────────
#
# Портирует модель ридера из примера (gen_tree.py): фокус-событие окружено
# событиями, делящими с ним грань (продукт/регион/тема). Канал ветки = первая
# общая грань по приоритету P→G→T. Фронт раскладывает: X=время, Y=ярус канала
# (тема — центр, география — вверх, продукт — вниз). Грани «игрок» (A) у нас нет.
# Сюжетная связь (ближайшие prev/next) рисуется толще.
_STORY_CAPS = {"P": 3, "G": 3, "T": 4}          # макс. веток на канал (как caps() в примере)
_CH_COLOR = {"P": "#1B7A3E", "G": "#D97706", "T": "#1E4FB0", "A": "#6E5BD6"}
_CH_LABEL = {"P": "продукт", "G": "регион", "T": "тема"}
_FOCUS_COLOR = "#15161A"


def _story_channel(focus_facets: dict[str, set[str]], cp: set[str], cg: set[str], cc: set[str]) -> tuple[str, str | None]:
    """Канал связи focus↔кандидат по приоритету P→G→T + общая сущность."""
    sp = focus_facets["p"] & cp
    if sp:
        return "P", sorted(sp)[0]
    sg = focus_facets["g"] & cg
    if sg:
        return "G", sorted(sg)[0]
    sc = focus_facets["c"] & cc
    if sc:
        return "T", sorted(sc)[0]
    return "T", None


def _facet_set(value: Any) -> set[str]:
    """Множество названий грани: JSONB-объект {name:''} → ключи, массив → элементы."""
    if isinstance(value, dict):
        return {str(k) for k in value.keys()}
    return set(_as_str_list(value, 40))


async def _story_facet_branches(focus_facets: dict[str, set[str]], fday: date, used: set[int]) -> list[dict[str, Any]]:
    """Обычные связанные новости (facet-ветки): события, делящие грань с фокусом,
    по всему таймлайну; на канал — топ ближайших по времени (cap), баланс прошлое/будущее."""
    events_t = event_table_identifier("events")
    overlap_clauses = []
    params: dict[str, Any] = {}
    if focus_facets["p"]:
        overlap_clauses.append(sql.SQL("e.products ?| %(prods)s::text[]"))
        params["prods"] = list(focus_facets["p"])
    if focus_facets["g"]:
        overlap_clauses.append(sql.SQL("e.regions ?| %(regs)s::text[]"))
        params["regs"] = list(focus_facets["g"])
    if focus_facets["c"]:
        overlap_clauses.append(sql.SQL("e.topics ?| %(tops)s::text[]"))
        params["tops"] = list(focus_facets["c"])
    if not overlap_clauses:
        return []

    cand_rows = await fetch_all(
        sql.SQL(
            """
            SELECT e.id, e.date_from, e.title, e.sigma, e.topics, e.regions, e.products, e.main_news_id
            FROM {events} e
            WHERE e.status = 'active' AND ( {overlap} )
            ORDER BY e.date_from
            LIMIT 1500
            """
        ).format(events=events_t, overlap=sql.SQL(" OR ").join(overlap_clauses)),
        params,
    )

    groups: dict[str, list[dict[str, Any]]] = {"P": [], "G": [], "T": []}
    for r in cand_rows:
        rid = int(r["id"])
        nd = r.get("date_from")
        if rid in used or not isinstance(nd, datetime):
            continue
        ch, lab = _story_channel(
            focus_facets,
            set(_as_str_list(r.get("products"), 30)),
            set(_as_str_list(r.get("regions"), 30)),
            set(_as_str_list(r.get("topics"), 30)),
        )
        groups[ch].append({
            "id": str(rid), "date": nd.date().isoformat(),
            "title": (r.get("title") or "").strip(), "sigma": int(r.get("sigma") or 0),
            "ch": ch, "lab": lab, "color": _CH_COLOR.get(ch, "#1E4FB0"),
            "main_news_id": r.get("main_news_id"), "role": "facet", "story": False,
            "_dd": (nd.date() - fday).days,
        })

    out: list[dict[str, Any]] = []
    for ch, cap in _STORY_CAPS.items():
        items = groups.get(ch, [])
        later = sorted([x for x in items if x["_dd"] >= 0], key=lambda x: (x["_dd"], -x["sigma"]))
        earlier = sorted([x for x in items if x["_dd"] < 0], key=lambda x: (-x["_dd"], -x["sigma"]))
        cap_l = -(-cap // 2)
        cap_e = cap - cap_l
        if len(later) < cap_l:
            cap_e += cap_l - len(later)
            cap_l = len(later)
        if len(earlier) < cap_e:
            cap_l = min(len(later), cap_l + (cap_e - len(earlier)))
            cap_e = len(earlier)
        out.extend(later[:cap_l] + earlier[:cap_e])
    return out


async def event_story(news_id: int) -> dict[str, Any]:
    """Сюжетный эго-граф для ЛЮБОЙ новости.

    Фокус = событие новости, если оно есть (тогда показываем сюжетную цепочку sprev/snext
    из дерева + facet-ветки). Если новость не входит ни в одно событие — фокусом становится
    САМА новость, и вокруг неё строятся facet-ветки по граням (так таймлайн есть у всех).
    Формат: {focus, story, nodes[]} (узел: ch P/G/T, lab, role, story).
    """
    cache_key = ("event_story_v6", int(news_id))
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    await ensure_event_schema()
    events_t = event_table_identifier("events")
    sources_t = event_table_identifier("event_sources")
    stories_t = event_table_identifier("event_stories")
    empty = {"focus": None, "story": None, "nodes": []}

    # 1. Пытаемся найти событие новости (по источникам или как главную).
    focus_row = await fetch_one(
        sql.SQL(
            """
            SELECT e.id, e.date_from, e.title, e.sigma, e.topics, e.regions, e.products,
                   e.main_news_id, e.story_id, e.story_parent_id
            FROM {events} e
            WHERE e.status = 'active'
              AND ( e.main_news_id = %(nid)s
                    OR EXISTS (SELECT 1 FROM {sources} s
                               WHERE s.event_id = e.id AND s.news_id = %(nid)s) )
            ORDER BY e.sigma DESC, e.sources_count DESC
            LIMIT 1
            """
        ).format(events=events_t, sources=sources_t),
        {"nid": int(news_id)},
    )

    chain: list[dict[str, Any]] = []
    used: set[int] = set()
    fstory_id = None
    focus_node: dict[str, Any]
    focus_facets: dict[str, set[str]]
    fday: date

    if focus_row and isinstance(focus_row.get("date_from"), datetime):
        # ── фокус = событие ──
        fid = int(focus_row["id"])
        fdate: datetime = focus_row["date_from"]
        fday = fdate.date()
        fstory_id = focus_row.get("story_id")
        fparent_id = focus_row.get("story_parent_id")
        focus_facets = {
            "p": set(_as_str_list(focus_row.get("products"), 30)),
            "g": set(_as_str_list(focus_row.get("regions"), 30)),
            "c": set(_as_str_list(focus_row.get("topics"), 30)),
        }
        used.add(fid)
        focus_node = {
            "id": str(fid), "date": fday.isoformat(),
            "title": (focus_row.get("title") or "").strip(),
            "sigma": int(focus_row.get("sigma") or 0),
            "ch": None, "lab": None, "color": _FOCUS_COLOR,
            "main_news_id": focus_row.get("main_news_id"), "role": "focus", "story": False,
        }
        # сюжетная цепочка из дерева
        if fstory_id is not None:
            chain_rows = await fetch_all(
                sql.SQL(
                    """
                    SELECT e.id, e.date_from, e.title, e.sigma, e.topics, e.regions, e.products,
                           e.main_news_id
                    FROM {events} e
                    WHERE e.status = 'active' AND e.story_id = %(sid)s
                      AND ( e.id = %(parent)s OR e.story_parent_id = %(fid)s )
                    """
                ).format(events=events_t),
                {"sid": int(fstory_id), "parent": int(fparent_id) if fparent_id is not None else 0, "fid": fid},
            )
            for r in chain_rows:
                nd = r.get("date_from")
                if not isinstance(nd, datetime):
                    continue
                ch, lab = _story_channel(
                    focus_facets,
                    set(_as_str_list(r.get("products"), 30)),
                    set(_as_str_list(r.get("regions"), 30)),
                    set(_as_str_list(r.get("topics"), 30)),
                )
                role = "sprev" if fparent_id is not None and int(r["id"]) == int(fparent_id) else "snext"
                chain.append({
                    "id": str(int(r["id"])), "date": nd.date().isoformat(),
                    "title": (r.get("title") or "").strip(), "sigma": int(r.get("sigma") or 0),
                    "ch": ch, "lab": lab, "color": _CH_COLOR.get(ch, "#1E4FB0"),
                    "main_news_id": r.get("main_news_id"), "role": role, "story": True,
                    "_dd": (nd.date() - fday).days,
                })
                used.add(int(r["id"]))
    else:
        # ── фокус = сама новость (нет события) ──
        news_t = await news_table_identifier()
        nrow = await fetch_one(
            sql.SQL("SELECT id, date, title, topics, regions, products FROM {news} WHERE id = %(nid)s")
            .format(news=news_t),
            {"nid": int(news_id)},
        )
        if not nrow:
            return cache_set(cache_key, empty, 120)
        ndate = nrow.get("date")
        if isinstance(ndate, datetime):
            fday = ndate.date()
        elif isinstance(ndate, date):
            fday = ndate
        else:
            return cache_set(cache_key, empty, 120)
        focus_facets = {
            "p": _facet_set(nrow.get("products")),
            "g": _facet_set(nrow.get("regions")),
            "c": _facet_set(nrow.get("topics")),
        }
        if not (focus_facets["p"] or focus_facets["g"] or focus_facets["c"]):
            return cache_set(cache_key, empty, 120)
        focus_node = {
            "id": f"n{int(news_id)}", "date": fday.isoformat(),
            "title": (nrow.get("title") or "").strip(), "sigma": 70,
            "ch": None, "lab": None, "color": _FOCUS_COLOR,
            "main_news_id": int(news_id), "role": "focus", "story": False,
        }

    # 2. facet-ветки (общие для обоих путей)
    branches: list[dict[str, Any]] = list(chain) + await _story_facet_branches(focus_facets, fday, used)
    for b in branches:
        b.pop("_dd", None)
    if not branches:
        return cache_set(cache_key, empty, 120)

    # 3. Имя сюжета: из дерева, иначе из граней фокуса.
    story = None
    if fstory_id is not None:
        srow = await fetch_one(
            sql.SQL("SELECT name, color, size FROM {stories} WHERE id = %(sid)s").format(stories=stories_t),
            {"sid": int(fstory_id)},
        )
        if srow:
            story = {"name": srow.get("name") or "Сюжет",
                     "color": srow.get("color") or _CH_COLOR["T"],
                     "count": int(srow.get("size") or (len(branches) + 1))}
    if story is None:
        top_p = sorted(focus_facets["p"])[:1]
        top_c = sorted(focus_facets["c"])[:1]
        parts = [p for p in (top_p[0] if top_p else None, top_c[0] if top_c else None) if p]
        story = {"name": " · ".join(parts) if parts else "Связи новости",
                 "color": _CH_COLOR["P"] if top_p else _CH_COLOR["T"],
                 "count": len(branches) + 1}

    result = {"focus": focus_node, "story": story, "nodes": branches}
    return cache_set(cache_key, result, 120)
