# Воспроизводимая спецификация: новости → события → сюжеты

Этот документ — **самодостаточная инструкция**. По нему можно воспроизвести систему с нуля
(человек + Claude), не имея доступа к остальному репозиторию. Описаны: контракт входных данных,
полная схема БД, все алгоритмы (с формулами и кодом), параметры и порядок запуска.

**Что делает система.** Берёт поток разрозненных новостных постов (Telegram-каналы агрорынка),
группирует похожие публикации об одном инфоповоде в **события** (event), затем строит граф связей
между событиями и нарезает его на **сюжеты** (story) — деревья связанных событий, разворачивающихся
во времени.

**Гарантии и ограничения.**
- Без платных LLM, **CPU-only**. Одна локальная модель эмбеддингов + чистый Python.
- Детерминированность: при одинаковом входе и параметрах результат повторяем (модель эмбеддингов
  фиксирована по версии; никакой случайности в алгоритмах нет).
- Масштаб: рассчитано на десятки тысяч новостей и ~сотни–тысячи событий. Граф строится в памяти.

---

## 0. Архитектура конвейера

```
news_list (входная таблица, не наша — read-only источник)
   │
   │  ЭТАП 1: fetch_unprocessed_news()  — отбор качественных, не-дайджестов, ещё не обработанных
   ▼
[список строк новостей]
   │  ЭТАП 2: cluster_news_rows_semantic()  — похожие новости → группы (кластеры)
   ▼                                          (fallback: cluster_news_rows() без модели)
[группы новостей]
   │  ЭТАП 3: analyze_group_semantic_rag()  — синтез карточки события из группы
   ▼
[карточка: title, summary, topics/regions/products, impacts, sigma]
   │  ЭТАП 4: upsert_event_from_group()  — запись + гейтинг active/ignored_weak
   ▼
events / event_sources / event_impacts   (таблицы БД)
   │  ЭТАП 5: rebuild_event_graph()  — связи между событиями + нарезка на сюжеты-деревья
   ▼
event_links / event_stories / events.story_*
   │  ЭТАП 6: event_story(news_id)  — по запросу строит эго-граф сюжета вокруг новости
   ▼
JSON {focus, story, nodes[]}  →  фронтенд рисует таймлайн
```

Оркестратор (воркер) гоняет ЭТАПЫ 1–4 в цикле каждые `EVENT_WORKER_INTERVAL_SECONDS`, и при
появлении новых событий запускает ЭТАП 5. ЭТАП 6 — синхронный, по HTTP-запросу, с кэшем.

---

## 1. Технологический стек и зависимости

- **Python 3.12**, асинхронный код (`asyncio`).
- **PostgreSQL** (любая поддерживаемая версия с JSONB и оператором `?|`). Драйвер — `psycopg` 3 (async, pool).
- **Модель эмбеддингов**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  (384-мерные мультиязычные эмбеддинги, хорошо работает с русским).
- Веб-слой (для ЭТАПА 6) — любой; в оригинале FastAPI.

`requirements.txt` (API):
```
fastapi==0.115.6
uvicorn[standard]==0.32.1
psycopg[binary,pool]==3.2.3
pydantic==2.10.4
python-dotenv==1.0.1
Pillow==11.2.1
```

`requirements-worker.txt` (воркер, CPU-only torch — **не ставить CUDA на сервере**):
```
-r requirements.txt
--extra-index-url https://download.pytorch.org/whl/cpu
torch==2.5.1+cpu
numpy==1.26.4
sentence-transformers==3.3.1
```

> Если `sentence-transformers`/`torch` недоступны — система автоматически откатывается на
> offline-режим кластеризации (см. §7.3), который вообще не требует ML.

---

## 2. Контракт входных данных (`news_list`)

Источник новостей — внешняя таблица (например `public.news_list`). Используемые поля:

| Поле | Тип | Назначение |
|---|---|---|
| `id` | int (PK) | идентификатор новости |
| `date` | timestamp | дата публикации (по ней окна и сортировка) |
| `title` | text | заголовок |
| `text` | text | тело поста |
| `source` | text | канал/источник |
| `customer` | text | заказчик (запасной «источник» для подсчёта уникальности) |
| `link_site` | text | ссылка на оригинал |
| `link_photo` | text | картинка (для витрины, в кластеризации не используется) |
| `views` | int | просмотры (тай-брейк при выборе заголовка) |
| `topics` | JSONB | темы: `{"Пшеница":"","Экспорт":""}` **или** `["Пшеница","Экспорт"]` |
| `regions` | JSONB | регионы (тот же формат) |
| `products` | JSONB | продукты (тот же формат) |
| `tag`, `extra_tag`, `object` | text/JSONB | доп. теги (объединяются в общий набор) |

`topics/regions/products` — **грани** (facets) новости. Это ключевой вход и для кластеризации,
и для графа сюжетов. Если в вашем источнике их нет — нужно их откуда-то извлекать
(NER/словарь), иначе граф сюжетов деградирует до текстового сходства.

---

## 3. Схема БД (полный DDL)

Всё живёт в отдельной схеме (`EVENTS_SCHEMA`, по умолчанию `harvester_news`), чтобы не конфликтовать
с `news_list`. DDL идемпотентен (`IF NOT EXISTS`) — безопасно вызывать при каждом старте.

```sql
CREATE SCHEMA IF NOT EXISTS harvester_news;

-- Событие: кластер новостей об одном инфоповоде
CREATE TABLE IF NOT EXISTS harvester_news.events (
    id            BIGSERIAL PRIMARY KEY,
    event_key     TEXT NOT NULL UNIQUE,          -- стабильный ключ кластера (см. §8)
    title         TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active', -- 'active' | 'ignored_weak'
    sigma         INTEGER NOT NULL DEFAULT 50,    -- важность 50..97
    news_count    INTEGER NOT NULL DEFAULT 0,
    sources_count INTEGER NOT NULL DEFAULT 0,     -- уникальных источников
    views         INTEGER NOT NULL DEFAULT 0,
    date_from     TIMESTAMP WITHOUT TIME ZONE,    -- min/max даты публикаций кластера
    date_to       TIMESTAMP WITHOUT TIME ZONE,
    main_news_id  INTEGER,                        -- «главная» новость (топ по просмотрам)
    tags          JSONB NOT NULL DEFAULT '[]'::jsonb,
    topics        JSONB NOT NULL DEFAULT '[]'::jsonb,
    regions       JSONB NOT NULL DEFAULT '[]'::jsonb,
    products      JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_llm       JSONB,                          -- отладочные данные анализа
    model         TEXT,
    created_at    TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at  TIMESTAMP WITHOUT TIME ZONE,
    last_seen_at  TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- сюжетные поля (заполняет ЭТАП 5):
    story_id        BIGINT,   -- к какому сюжету относится событие
    story_parent_id BIGINT,   -- родитель в дереве сюжета (events.id)
    story_pos       INTEGER   -- позиция по времени внутри сюжета
);

-- Источники события (какие новости в него вошли)
CREATE TABLE IF NOT EXISTS harvester_news.event_sources (
    event_id  BIGINT NOT NULL REFERENCES harvester_news.events(id) ON DELETE CASCADE,
    news_id   INTEGER NOT NULL,
    news_date TIMESTAMP WITHOUT TIME ZONE,
    title TEXT, source TEXT, customer TEXT, link_site TEXT, snippet TEXT,
    views INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(event_id, news_id)
);

-- Влияние события по ролям (rule-based)
CREATE TABLE IF NOT EXISTS harvester_news.event_impacts (
    event_id BIGINT NOT NULL REFERENCES harvester_news.events(id) ON DELETE CASCADE,
    role  TEXT NOT NULL,   -- farmer|processor|trader|agroholding|exporter
    label TEXT NOT NULL,
    impact TEXT NOT NULL CHECK (impact IN ('positive','negative','neutral','watch')),
    summary TEXT NOT NULL DEFAULT '',
    action_hint TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(event_id, role)
);

-- Дедупликация обработки: какие новости воркер уже видел
CREATE TABLE IF NOT EXISTS harvester_news.event_news_state (
    news_id INTEGER PRIMARY KEY,
    news_date TIMESTAMP WITHOUT TIME ZONE,
    status TEXT NOT NULL DEFAULT 'seen',   -- clustered|skipped|seen
    reason TEXT NOT NULL DEFAULT '',
    processed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Состояние воркера (для UI/диагностики)
CREATE TABLE IF NOT EXISTS harvester_news.event_job_state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── Сюжетный граф (ЭТАП 5) ──
CREATE TABLE IF NOT EXISTS harvester_news.event_stories (
    id BIGSERIAL PRIMARY KEY,
    story_key TEXT NOT NULL UNIQUE,   -- md5 от id членов
    name  TEXT NOT NULL DEFAULT '',   -- «Пшеница · Экспорт»
    color TEXT NOT NULL DEFAULT '#1E4FB0',
    size  INTEGER NOT NULL DEFAULT 0,
    date_from TIMESTAMP WITHOUT TIME ZONE,
    date_to   TIMESTAMP WITHOUT TIME ZONE,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS harvester_news.event_links (
    from_id BIGINT NOT NULL REFERENCES harvester_news.events(id) ON DELETE CASCADE, -- раньше
    to_id   BIGINT NOT NULL REFERENCES harvester_news.events(id) ON DELETE CASCADE, -- позже
    weight  REAL NOT NULL DEFAULT 0,
    channel TEXT NOT NULL DEFAULT 'T',  -- P (продукт) | G (регион) | T (тема)
    lab     TEXT,                        -- конкретная общая сущность
    in_story BOOLEAN NOT NULL DEFAULT FALSE, -- ребро принадлежит дереву сюжета
    PRIMARY KEY(from_id, to_id)
);

-- Индексы
CREATE INDEX IF NOT EXISTS hn_events_date_idx   ON harvester_news.events (date_to DESC NULLS LAST, id DESC);
CREATE INDEX IF NOT EXISTS hn_events_status_idx ON harvester_news.events (status);
CREATE INDEX IF NOT EXISTS hn_events_story_idx  ON harvester_news.events (story_id);
CREATE INDEX IF NOT EXISTS hn_event_sources_news_idx  ON harvester_news.event_sources (news_id);
CREATE INDEX IF NOT EXISTS hn_event_sources_event_idx ON harvester_news.event_sources (event_id);
CREATE INDEX IF NOT EXISTS hn_event_links_from_idx ON harvester_news.event_links (from_id);
CREATE INDEX IF NOT EXISTS hn_event_links_to_idx   ON harvester_news.event_links (to_id);
```

(В оригинале есть ещё таблицы для предрасчёта статистики тем — `news_topic_marks`,
`topic_daily_stats` и т.п. — они не относятся к событиям/сюжетам и здесь опущены.)

---

## 4. Конфигурация (переменные окружения)

| Переменная | Деф. | Где используется |
|---|---|---|
| `EVENTS_SCHEMA` | `harvester_news` | имя схемы БД |
| `EVENT_ANALYSIS_MODE` | `semantic-rag` | режим кластеризации; иначе `offline-rag` |
| `EVENT_WORKER_INTERVAL_SECONDS` | `300` | период цикла воркера |
| `EVENT_WORKER_BATCH_SIZE` | `300` | макс. новостей за один прогон |
| `EVENT_WORKER_FETCH_LIMIT` | `1000` | лимит выборки из `news_list` |
| `EVENT_WORKER_LOOKBACK_DAYS` | `365` | глубина выборки от последней даты данных |
| `EVENT_WORKER_PROCESS_ALL` | `false` | игнорировать окно дат (обработать всё) |
| `EVENT_MIN_SOURCES` | `3` | мин. уникальных источников → `active` |
| `EVENT_CONTEXT_SOURCES_LIMIT` | `12` | сколько источников учитывать в summary |
| **Семантика** | | |
| `SEMANTIC_RAG_ENABLED` | `true` | включить семантический режим |
| `SEMANTIC_EMBEDDING_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | модель |
| `SEMANTIC_DEVICE` | `cpu` | устройство |
| `SEMANTIC_BATCH_SIZE` | `32` | batch эмбеддинга |
| `SEMANTIC_CLUSTER_WINDOW_DAYS` | `5` | окно склейки новостей по дате |
| `SEMANTIC_CLUSTER_MIN_COSINE` | `0.66` | мин. косинус для связи |
| `SEMANTIC_CLUSTER_STRONG_COSINE` | `0.82` | «сильная» семантика |
| `SEMANTIC_MIN_TITLE_OVERLAP` | `0.18` | мин. совпадение заголовков (якорь) |
| `SEMANTIC_MIN_TOKEN_OVERLAP` | `0.12` | мин. совпадение токенов текста |
| `SEMANTIC_MIN_CLUSTER_COHESION` | `0.64` | мин. когезия кластера |
| `SEMANTIC_MAX_CLUSTER_SIZE` | `6` | макс. источников в событии |
| `SEMANTIC_EXCLUDE_DIGEST_SOURCES` | `true` | не делать дайджесты ядрами |
| **Offline-fallback** | | |
| `EVENT_CLUSTER_WINDOW_DAYS` | `5` | окно близости по дате |
| `EVENT_CLUSTER_MIN_SIMILARITY` | `0.24` | порог присоединения к кластеру |

Константы графа сюжетов (в коде, §10): `WIN_DAYS=21`, `WE=0.6`, `WT=0.4`, `TH=0.34`,
`SIZE_CAP=12`, `CHILD_CAP=4`, `MIN_STORY=3`, `TOPK_LINKS=16`.
Лимиты веток рендера (§11): `_STORY_CAPS={P:3, G:3, T:4}`.

---

## 5. Вспомогательные функции

```python
import re, math
from collections import Counter

_STOPWORDS = {  # русские служебные + новостной шум
    "и","в","во","на","по","с","со","к","ко","за","из","от","до","для","о","об","обо",
    "что","как","это","его","ее","её","их","или","а","но","при","над","под","после","перед",
    "россии","рф","россия","новости","сегодня","вчера","сообщил","сообщили","заявил","заявили",
    "года","год","месяца","недели","дня","тыс","млн","руб","рублей","тонн","тонны","тонну",
    "будет","были","было","быть","есть","также","уже","еще","ещё","может","могут","стал","стала",
}
_TOKEN_RE = re.compile(r"[a-zа-яё0-9]{3,}", re.IGNORECASE)   # токены ≥3 символов
_SENT_RE  = re.compile(r"(?<=[.!?…])\s+|\n+", re.UNICODE)    # разбиение на предложения

def _clean_text(value, max_len=600) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:max_len].strip()

def _as_str_list(value, limit=20) -> list[str]:
    """JSONB-грань → список строк. {name:''} → ключи; [..] → элементы; уникализация."""
    if isinstance(value, dict):
        raw = list(value.keys())
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    elif value is None:
        raw = []
    else:
        raw = [value]
    out = []
    for item in raw:
        t = _clean_text(item, 80)
        if t and t not in out:
            out.append(t)
        if len(out) >= limit:
            break
    return out

def _tokens(text, limit=80) -> list[str]:
    """Нормализованные токены: lower, ё→е, без стоп-слов и чистых чисел, без повторов."""
    seen, out = set(), []
    for m in _TOKEN_RE.finditer((text or "").casefold().replace("ё","е")):
        w = m.group(0)
        if w in _STOPWORDS or w.isdigit() or w in seen:
            continue
        seen.add(w); out.append(w)
        if len(out) >= limit:
            break
    return out

def _jaccard(a: set, b: set) -> float:
    if not a or not b: return 0.0
    return len(a & b) / max(1, len(a | b))

def _date_gap_days(a, b) -> int:
    from datetime import datetime
    if not isinstance(a, datetime) or not isinstance(b, datetime): return 9999
    return abs((a.date() - b.date()).days)
```

`_news_record(row)` приводит сырую строку к рабочей записи:
```python
def _news_record(row) -> dict:
    title = _clean_text(row.get("title"), 240)
    text  = _clean_text(row.get("text") or row.get("summary"), 2000)
    tags  = extract_tags(row.get("tag"), row.get("extra_tag"), row.get("object"),
                         row.get("topics"), row.get("regions"), row.get("products"))
    token_list = _tokens(" ".join([title, text, " ".join(tags)]), 100)
    return {
        "row": row, "id": int(row["id"]), "date": row.get("date"),
        "title": title, "text": text,
        "source": row.get("source") or row.get("customer") or "",
        "views": int(row.get("views") or 0),
        "tags": tags, "tokens": set(token_list), "token_list": token_list,
        "topics":   _as_str_list(row.get("topics"), 20),
        "regions":  _as_str_list(row.get("regions"), 20),
        "products": _as_str_list(row.get("products"), 20),
    }
```
(`extract_tags` — просто объединение всех тегов/граней в один уникальный список строк.)

---

## 6. ЭТАП 1 — отбор новостей

`fetch_unprocessed_news()` выбирает из `news_list` строки, которые:

1. Проходят **фильтр качества** `QUALITY_WHERE` (SQL):
```sql
NULLIF(BTRIM(COALESCE(n.title,'')),'') IS NOT NULL
AND LOWER(BTRIM(COALESCE(n.title,''))) NOT IN ('без заголовка','без названия','нет заголовка','none','null','-')
AND NOT (BTRIM(COALESCE(n.title,'')) ~* '^(https?://|www[.]|t[.]me/|@)')
AND NULLIF(BTRIM(COALESCE(n.text,'')),'') IS NOT NULL
AND CHAR_LENGTH(BTRIM(regexp_replace(COALESCE(n.text,''),'[[:space:]]+',' ','g'))) >= 50
AND CHAR_LENGTH(regexp_replace(COALESCE(n.text,''),'[^A-Za-zА-Яа-яЁё]+','','g')) >= 30
-- + отсев чисто ссылочных постов и сервисных хвостов «telegram | max», «дашборд ksm»
```
2. **Не дайджесты** — заголовок не матчит `LIKE ANY(...)` по списку:
   `%дайджест%`, `%самое интересное за день/неделю%`, `%главные новости%`, `%новости рынка на%`,
   `%итоги дня/недели%`, `%подборка новостей%`, `%результаты состоявшихся торгов%`,
   `%главпахарь: самое интересное%`, `%главагроном: самое интересное%`.
   *Зачем:* дайджест содержит десятки инфоповодов и склеивает несвязанные события в один кластер.
3. **Ещё не обработаны** — нет в `event_sources` (не вошли в событие) и нет в `event_news_state`
   (не помечены ранее).
4. В окне дат: `n.date >= (последняя_дата_данных − LOOKBACK_DAYS)` (если `PROCESS_ALL=false`).
   Берётся именно последняя дата **в данных**, не системная — чтобы работать с историческими срезами.

Сортировка: `date DESC, id DESC`. Лимит — `EVENT_WORKER_FETCH_LIMIT`.

---

## 7. ЭТАП 2 — кластеризация новостей в группы

### 7.1. Выбор режима
```python
mode = EVENT_ANALYSIS_MODE.lower()
if mode in {"semantic-rag","semantic","local-semantic-rag"} and semantic_available():
    clusters = cluster_news_rows_semantic(rows)   # §7.2
    analyzer = analyze_group_semantic_rag
else:
    clusters = cluster_news_rows(rows)             # §7.3
    analyzer = analyze_group_offline_rag
```
`semantic_available()` = модель удалось загрузить. Любая ошибка загрузки/энкодинга → fallback.

### 7.2. Семантический режим (основной)

**(а) Текст для эмбеддинга** — осознанно «узкий», без тегов/граней (иначе любые две новости
«про АПК» ложно близки):
```python
def _record_text(rec) -> str:
    title = _strip_emoji_and_noise(rec["title"])
    lead  = " ".join(_split_sentences(_strip_emoji_and_noise(rec["text"]))[:3])  # первые 3 предложения
    return _clean_text(f"query: {title}. {title}. {lead}", 1100)   # заголовок намеренно удвоен
```
Эмбеддинги нормируются (L2), поэтому косинус = скалярное произведение.

**(б) Пометка дайджестов** `_is_digest_like(rec)` (на случай, если просочились): шаблоны заголовка,
≥18 тегов, ≥4 маркеров списка (`* 🔹 🔸 ▪ •`), служебные фразы. Если `SEMANTIC_EXCLUDE_DIGEST_SOURCES`,
дайджесты не становятся ядрами кластеров.

**(в) Правило связи двух новостей** — ядро логики:
```python
def _can_link(a, b, sem) -> (bool, score, reason):
    if _date_gap_days(a["date"], b["date"]) > SEMANTIC_CLUSTER_WINDOW_DAYS:
        return False, 0.0, "date_gap"

    title_a, title_b = _title_anchor_tokens(a), _title_anchor_tokens(b)  # токены заголовка без generic
    tags_a,  tags_b  = _tag_set(a), _tag_set(b)                          # теги без generic
    token_score = _jaccard(a["tokens"], b["tokens"])
    title_score = _jaccard(title_a, title_b)
    tag_score   = _jaccard(tags_a, tags_b)
    shared_anchor = bool(title_a & title_b)

    # Дайджест соединяется только с почти таким же заголовком
    if (a["digest_like"] or b["digest_like"]) and title_score < 0.55:
        return False, sem, "digest_guard"
    if sem < SEMANTIC_CLUSTER_MIN_COSINE:
        return False, sem, "low_cosine"

    has_title = title_score >= SEMANTIC_MIN_TITLE_OVERLAP or shared_anchor
    has_token = token_score >= SEMANTIC_MIN_TOKEN_OVERLAP

    # сильная семантика + любой якорь
    if sem >= SEMANTIC_CLUSTER_STRONG_COSINE and (has_title or has_token):
        return True, 0.70*sem + 0.18*title_score + 0.08*token_score + 0.04*tag_score, "strong"
    # средняя семантика, но есть И заголовочный, И токенный якорь
    if has_title and has_token:
        return True, 0.66*sem + 0.20*title_score + 0.10*token_score + 0.04*tag_score, "title_token"
    # общий тег + слабый заголовочный якорь
    if (tags_a & tags_b) and title_score >= max(0.22, SEMANTIC_MIN_TITLE_OVERLAP) and token_score >= 0.14:
        return True, 0.62*sem + 0.18*title_score + 0.10*token_score + 0.10*tag_score, "tag_with_title"
    return False, sem, "no_event_anchor"
```
`_title_anchor_tokens` и `_tag_set` выкидывают «generic» сущности (`россия, аналитика, цфо, европа,
технологии, зерновые, масличные, …`) — они не должны быть причиной склейки.

**(г) Назначение новости в кластер** `_best_cluster_for_record`:
```python
for cluster in clusters:
    if len(cluster) >= max(2, SEMANTIC_MAX_CLUSTER_SIZE): continue
    cohesion = mean(cosine(rec, item) for item in cluster)        # средняя близость к членам
    if cohesion < SEMANTIC_MIN_CLUSTER_COHESION: continue
    # связь проверяется с первыми ≤4 членами (seed), не с одним случайным:
    ok_links = [_can_link(rec, item, cosine(rec,item)) for item in cluster[:4] if ok]
    if not ok_links: continue
    score = best(ok_links)
    if len(cluster) >= 3 and len(ok_links) < 2 and score < 0.86: continue  # большому кластеру нужно 2 связи
    score = 0.82*score + 0.18*cohesion
    # выбираем кластер с макс. score; если ни один не подошёл — новый кластер
```
Проверка связи с *несколькими* членами убирает «transitive-chain overmerge» (A~B, B~C ⇒ A в кластер C).

**(д) Финальный сплит.** Каждый кластер ≥3 ещё раз прогоняется тем же назначением по seed-ам —
если внутри оказались разные заголовочные ядра, кластер раскалывается.

Полный цикл `cluster_news_rows_semantic`:
```python
records = [_news_record(r) for r in rows if r.title or r.text]
records.sort(key=lambda r:(r.date, r.views, r.id), reverse=True)
for r in records: r["digest_like"] = _is_digest_like(r)
primary = [r for r in records if not r["digest_like"]] or records[:1]
for r, v in zip(primary, embed([_record_text(r) for r in primary])): r["embedding"] = l2norm(v)
clusters = []
for r in primary:
    i = _best_cluster_for_record(r, clusters)
    clusters[i].append(r) if i >= 0 else clusters.append([r])
clusters = refine_split(clusters)   # (д)
return [[r["row"] for r in sorted(c, by views,date desc)] for c in clusters]
```

### 7.3. Offline-режим (fallback, без модели)

Инкрементальные профили кластеров (счётчики токенов/тегов, наборы токенов заголовков, даты).
Сходство новости с профилем:
```
score = 0.48·jaccard(tokens, top-50 токенов кластера)
      + 0.30·jaccard(tags,   top-30 тегов кластера)
      + 0.12·max jaccard(title_tokens, заголовки кластера)
      + 0.10·date_proximity        # 1 − gap/(WINDOW+1), 0 если gap > WINDOW
```
Новость идёт в лучший кластер, если `score ≥ EVENT_CLUSTER_MIN_SIMILARITY` (0.24), иначе — новый.

---

## 8. ЭТАП 3 — синтез карточки события

`analyze_group_semantic_rag(group)` (или offline-аналог) превращает группу новостей в карточку:

- **`event_key`** — стабильный хеш кластера (`_event_key_from_cluster`):
  топ-8 токенов + топ-6 тегов (отсортированы) + «бакет даты» `min_ordinal // WINDOW`,
  затем `sha1(...)[:24]`. Один и тот же инфоповод → один ключ → upsert вместо дубля.
- **`title`** — `_best_semantic_title`: член группы, чей эмбеддинг ближе к центроиду кластера
  (+ бонусы за длину 35–150, наличие источника, log(views)). Offline — эвристика по длине/просмотрам.
- **`summary`** — `_semantic_summary`: extractive + MMR. Кандидаты-предложения (55–420 символов,
  без рекламы/ссылок) ранжируются по близости к центроиду; берутся 3, дубли отсекаются (jaccard > 0.55).
- **`topics/regions/products`** — агрегаты граней по частоте среди членов.
- **`impacts`** — rule-based по ролям (см. ниже).
- **`sigma` (50–97)** — `_semantic_sigma`: база от числа публикаций и уникальных источников,
  скорректированная **когезией** кластера (средний попарный косинус): `<0.55` → штраф −16,
  `>0.78` → бонус +5; одиночка ограничена 68.

База sigma (offline и как основа semantic):
```python
score = 52 + min(24, n_records*3) + min(18, n_sources*5)
score += 4 if n_sources >= 2 else 0
score += 3 if n_records >= 4 else 0
sigma = max(50, min(96, score))
```

**Impacts по ролям** `_role_impacts(tags, title)` — детектируем флаги по подстрокам в
`title + теги` (`price`, `export`, `reg`, `weather`, `deal`) и выдаём по записи на роль
(`farmer`, `processor`, `trader`, `exporter`, [`agroholding`]) с `impact ∈ {positive,negative,neutral,watch}`,
коротким `summary` и `action_hint`. Пример: `weather=True` → фермеру `negative`
(«погодный фактор может изменить урожайность…»); `export∨reg` → экспортёру `watch`.

Результат анализа — dict: `{title, summary, tags, topics, regions, products, impacts, sigma, raw_llm}`.

---

## 9. ЭТАП 4 — запись события и гейтинг

`upsert_event_from_group(event_key, rows, analysis)`:

1. **Upsert в `events`** по `event_key` (`ON CONFLICT DO UPDATE`). `main_news_id` — новость с макс.
   просмотрами. `date_from/date_to` = min/max дат группы. `views` = сумма.
2. **Upsert источников** в `event_sources` (по `(event_id, news_id)`).
3. **Перезапись `event_impacts`** (DELETE + INSERT по ролям).
4. **Пересчёт агрегатов по фактическим источникам** и финальный гейтинг:
```sql
UPDATE events e SET
  news_count    = agg.news_count,
  sources_count = agg.sources_count,
  status = CASE WHEN agg.sources_count >= :EVENT_MIN_SOURCES THEN 'active' ELSE 'ignored_weak' END,
  views = agg.views, date_from = agg.date_from, date_to = agg.date_to
FROM (
  SELECT event_id, COUNT(*) news_count,
         COUNT(DISTINCT COALESCE(NULLIF(source,''),NULLIF(customer,''),news_id::text)) sources_count,
         SUM(views) views, MIN(news_date) date_from, MAX(news_date) date_to
  FROM event_sources WHERE event_id = :id GROUP BY event_id
) agg WHERE e.id = agg.event_id;
```
**Уникальность источника** = `source` (или `customer`, или `news_id` как запасной). Событие с
`sources_count < EVENT_MIN_SOURCES` (3) помечается `ignored_weak` и **не отдаётся API** (но
хранится, чтобы не переобрабатывать те же новости).

После прогона batch все новости помечаются в `event_news_state`
(`clustered`/`skipped`) — это дедупликация ЭТАПА 1.

---

## 10. ЭТАП 5 — граф связей событий и сюжеты

`rebuild_event_graph()` — отдельный модуль. Запускается после ЭТАПА 4, если события менялись.
Вся работа **в памяти**. Это порт алгоритма `soft.py`.

**Параметры:** `WIN_DAYS=21, WE=0.6, WT=0.4, TH=0.34, SIZE_CAP=12, CHILD_CAP=4, MIN_STORY=3, TOPK_LINKS=16`.

### Шаг 1. Загрузка и векторизация
```python
rows = SELECT id, date_from, title, summary, topics, regions, products
       FROM events WHERE status='active' AND date_from IS NOT NULL ORDER BY date_from, id
N = len(rows)
df, tdf = Counter(), Counter()        # document frequency граней и токенов текста
for r in rows:
    p,g,c = set(facets products/regions/topics)
    facets = p | g | c
    toks = [t for t in _TOKEN_RE.findall((title+' '+summary).lower()) if t not in _STOPWORDS]
    ev = {id, day:date_from, p,g,c, tf:Counter(toks), facets}
    for x in facets: df[x]+=1
    for t in set(toks): tdf[t]+=1

IDF[x]  = log((N+1)/(df[x]+1)) + 1            # редкая грань весит больше
tIDF[t] = log((N+1)/(tdf[t]+1)) + 1
for ev:
    ev.tvec  = {t:(1+log(f))*tIDF[t] for t,f in ev.tf}   # TF-IDF текста
    ev.tnorm = ||tvec||
    ev.fnorm = sqrt(Σ IDF[x]² for x in facets)
```

### Шаг 2. Сила связи двух событий
```python
def soft(a, b):                       # IDF-взвешенный косинус по общим граням
    shared = a.facets & b.facets
    if not shared: return 0.0
    return Σ IDF[x]² for x in shared / (a.fnorm * b.fnorm)

def txt(a, b):                        # разреженный косинус TF-IDF текста
    return Σ a.tvec[t]*b.tvec[t] / (a.tnorm * b.tnorm)

s(a,b) = WE*soft(a,b) + WT*txt(a,b)   # 0.6 / 0.4
```

### Шаг 3. Рёбра в окне времени (sweep по отсортированным по дате)
```python
edges = []
for i in range(N):
    for j in range(i+1, N):
        if (ev[j].day - ev[i].day).days > WIN_DAYS: break   # окно 21 день
        if soft(ev[i],ev[j]) == 0: continue                  # нет общих граней — пропуск (дёшево)
        s = WE*soft + WT*txt
        if s < TH: continue                                  # порог ребра 0.34
        ch, lab = channel(ev[i], ev[j])                      # приоритет P→G→T, общая сущность
        edges.append((i, j, round(s,4), ch, lab))            # i раньше j: направление earlier→later
```
`channel(a,b)`: если есть общий продукт → `("P", min(общие продукты))`; иначе регион → `"G"`;
иначе тема → `"T"`; иначе `("T", None)`.

### Шаг 4. Жадный ветвящийся лес → сюжеты
```python
parent, children, comp_size = {}, defaultdict(list), {}
def root(n):
    while n in parent: n = parent[n]
    return n
for x, y, w, ch, lab in sorted(edges, key=-weight):   # по убыванию веса
    if y in parent: continue                          # у узла ≤1 родитель
    if len(children[x]) >= CHILD_CAP: continue        # ≤4 детей
    rx = root(x)
    if rx == y: continue                              # без циклов
    if comp_size.get(rx,1) + comp_size.get(y,1) > SIZE_CAP: continue  # дерево ≤12
    parent[y] = x; children[x].append(y); comp_size[rx] += comp_size.get(y,1)

groups = компоненты по root(); сюжет = компонента с len ≥ MIN_STORY (3)
```
Для каждого сюжета:
- члены сортируются по `(day, id)`;
- **имя** = `топ-продукт · топ-тема` среди членов (generic-темы `{Экспорт, Импорт, Регулирование,
  Аналитика, Цена, Господдержка, Торговля, Логистика, Мероприятия, Россия, Прочее}` исключены);
- **цвет** — из палитры по индексу; **`story_key`** = `"s:" + md5(sorted ids)[:16]`;
- `date_from/date_to` = даты первого/последнего члена.

### Шаг 5. Хранение (одна транзакция)
```python
# top-K рёбер на узел (16), чтобы не хранить всё
incident[node] += edge_index;  keep = ⋃ top16(incident[node] by weight)
story_edges = {(x,y) for y,x in parent.items()}   # рёбра дерева

TRUNCATE event_links
TRUNCATE event_stories RESTART IDENTITY
UPDATE events SET story_id=NULL, story_parent_id=NULL, story_pos=NULL WHERE story_id IS NOT NULL
for story:
    INSERT INTO event_stories(story_key,name,color,size,date_from,date_to) RETURNING id
    for pos, member in enumerate(members):
        story_id=id, story_parent_id = events.id родителя (если родитель тоже в сюжете), story_pos=pos
executemany INSERT INTO event_links(from_id,to_id,weight,channel,lab,in_story)  # in_story=(x,y)∈story_edges
COMMIT
```

Возврат: `{events, edges_total, edges_stored, stories, events_in_story}`.
Типичный прогон: 707 событий → 795 связей, 64 сюжета, 378 событий в сюжетах.

---

## 11. ЭТАП 6 — рендер сюжета по запросу

`event_story(news_id)` (кэш 120 c, ключ `v6`) строит **эго-граф** вокруг новости.
Ответ: `{focus, story, nodes[]}`; узел = `{id, date, title, sigma, ch, lab, color, main_news_id, role, story}`,
`role ∈ {focus, sprev, snext, facet}`, `ch ∈ {P,G,T}`.

1. **Резолв фокуса.** Ищем событие новости: `events.main_news_id = nid` **или** есть в `event_sources`.
   Берём активное с макс. `sigma`/`sources_count`.
   - **Есть событие** → фокус = событие. Если у него `story_id`, тянем цепочку из дерева:
     `sprev` = родитель (`id = story_parent_id`), `snext` = дети (`story_parent_id = focus.id`)
     в рамках того же `story_id`. Эти узлы `story=true`.
   - **Нет события** → фокус = сама новость (`id="n{news_id}"`, грани из `news_list`),
     сюжетной цепочки нет.
2. **Facet-ветки** `_story_facet_branches`: события из всего таймлайна, делящие с фокусом грань
   (SQL-оператор `?|` по products/regions/topics). Канал — по приоритету P→G→T. На канал — лимит
   `_STORY_CAPS = {P:3, G:3, T:4}`, с балансом прошлое/будущее относительно даты фокуса
   (половина веток — позже, половина — раньше). Эти узлы `role="facet"`, `story=false`.
3. **Имя сюжета**: из `event_stories` по `story_id`, иначе `топ-продукт · топ-тема` граней фокуса.
4. Если веток нет — возвращаем пустой результат (таймлайн не рисуется).

---

## 12. Оркестрация (воркер)

```python
async def _run_once():
    topic_result = await sync_topic_index_once(...)        # опционально: индексация тем (не для событий)
    result = await process_events_once()                   # ЭТАПЫ 1–4
    if result["events_upserted"] > 0:
        result["story_graph"] = await rebuild_event_graph()  # ЭТАП 5
    await write_job_state("events_worker_last_run", {...})
    return result

# Цикл: while not stop: _run_once(); sleep(EVENT_WORKER_INTERVAL_SECONDS)
```

CLI-флаги воркера:
- `--once` — один batch и выход; `--drain` — гнать batch-и, пока есть новые новости.
- `--reset` — `TRUNCATE` всех event-таблиц (полная пересборка новым алгоритмом).
- `--rebuild-stories` — только пересчитать граф/сюжеты на текущих событиях.

`process_events_once()`:
```python
rows = await fetch_unprocessed_news()           # ЭТАП 1
rows = rows[:EVENT_WORKER_BATCH_SIZE]
clusters = cluster_*(rows)                       # ЭТАП 2
for group in clusters:
    key = _event_key_from_cluster(group)
    analysis = await analyzer(key, group)        # ЭТАП 3
    await upsert_event_from_group(key, group, analysis)  # ЭТАП 4
await mark_event_news_batch(rows, clustered_ids) # дедуп
```

---

## 13. Чеклист воспроизведения

1. Поднять PostgreSQL; подготовить/подключить таблицу-источник `news_list` по контракту §2.
2. Установить зависимости (§1). Для семантики — CPU-torch + sentence-transformers.
3. Прогнать DDL (§3) — создать схему и таблицы (идемпотентно, можно при каждом старте).
4. Задать env (§4). Минимум: строка подключения к БД, `EVENTS_SCHEMA`, `EVENT_ANALYSIS_MODE`.
5. Реализовать функции §5–§11 (помощники → ЭТАПЫ 1–6). Критичные «умные» части — это
   `_can_link`/`_best_cluster_for_record` (§7.2) и `rebuild_event_graph` (§10); остальное — плумбинг.
6. Запустить воркер: `python -m app.workers.events_worker --reset --once --drain` (первая полная сборка),
   далее — как сервис в цикле.
7. Поднять HTTP-эндпоинт `GET /news/{id}/story` поверх `event_story` (§11).

### Проверка
```sql
SELECT count(*) FROM harvester_news.events WHERE status='active';
SELECT count(*) FROM harvester_news.event_links;
SELECT count(*) FROM harvester_news.event_stories;
SELECT count(*) FROM harvester_news.events WHERE story_id IS NOT NULL;
-- разрез по каналам связей:
SELECT channel, count(*) FROM harvester_news.event_links GROUP BY channel;
```
Ожидаемо: события собираются в кластеры по 1–6 источников; ~10% событий попадают в сюжеты;
рёбра преимущественно по каналам P/G; имена сюжетов вида «Пшеница · Экспорт».

### Типичные ошибки воспроизведения
- **Грани не извлекаются** (`topics/regions/products` пустые) → граф сюжетов вырождается. Нужен
  источник граней (NER/словарь).
- **Не исключены дайджесты** → гигантские мусорные кластеры. Проверьте §6 и `_is_digest_like`.
- **Слишком низкие пороги** (`MIN_COSINE`, `TH`) → overmerge; слишком высокие → всё распадается на одиночки.
- **В эмбеддинг добавили теги/грани** → ложная близость «всё про АПК». Текст для эмбеддинга — только
  заголовок + лид (§7.2а).

---

## 14. Карта файлов оригинала (для сверки)

| Файл | Содержимое |
|---|---|
| `app/services/event_tables.py` | DDL (§3), `ensure_event_schema`, job-state |
| `app/services_events.py` | помощники (§5), ЭТАП 1 (`fetch_unprocessed_news`), offline-кластеризация (§7.3), синтез offline (§8), `upsert_event_from_group` (§9), `process_events_once`, `event_story` (§11) |
| `app/services/semantic_rag.py` | семантическая кластеризация и синтез (§7.2, §8) |
| `app/services/event_graph.py` | граф связей и сюжеты (§10) |
| `app/services_news.py` | `QUALITY_WHERE`, `NEWS_COLUMNS`, `_role_impacts` (§6, §8) |
| `app/workers/events_worker.py` | оркестрация и CLI (§12) |
| `app/config.py` | параметры (§4) |
| `frontend/src/components/StoryTimeline.tsx` | визуализация эго-графа сюжета |
