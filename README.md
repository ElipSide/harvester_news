# Harvester News — production + semantic RAG события без платных LLM

Проект публикуется под префиксом `/test_news/` за внешним nginx, например:

```text
https://your-domain.example/test_news/
```

В Docker запускаются три сервиса:

```text
backend        FastAPI API
frontend       nginx со статикой React
events-worker  фоновая semantic-RAG склейка новостей в события без платных LLM
```

Локальная PostgreSQL в `docker-compose` не используется. Backend и worker подключаются к вашей БД через `PG_CONNINFO`.

## Как работает слой событий

Пользовательский интерфейс не запускает анализ и не ждёт обработки. Всё делает worker по расписанию:

```text
news_list
  → фильтр качества
  → локальные multilingual embeddings
  → guarded semantic clustering похожих публикаций
  → semantic extractive summary по найденным источникам
  → rule-based impact по ролям
  → harvester_news.events / event_sources / event_impacts
  → frontend читает готовые события из таблиц
```

Платные LLM, OpenAI API и внешние embedding API не используются. Модель embeddings — open-source `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, запускается локально внутри `events-worker`. При первом запуске Docker скачает модель в volume `test_news_semantic_models`.

## Как формируются события (вкратце)

Человеческое объяснение алгоритма — как из потока новостей рождается «событие»:

1. **Берём свежие новости.** Из общего потока (`news_list`) — только те, что ещё не обрабатывали. Сразу отсекаем мусор: дайджесты, подборки «итоги дня», рекламные хвосты — они склеивают всё подряд.

2. **Переводим каждую новость в «смысловой отпечаток».** Локальная мультиязычная модель превращает заголовок + начало текста в вектор (эмбеддинг). Это улавливает *смысл*, а не совпадение слов: «пошлина на пшеницу выросла» и «экспортная пошлина повышена» окажутся рядом, хотя слова разные.

3. **Сравниваем новости попарно и группируем похожие.** Две новости попадают в одно событие, если выполняются **оба** условия:
   - близки по смыслу (высокий косинус векторов);
   - есть **общий «якорь»** — одно и то же имя, объект, мера или цифра в заголовке/тексте.

   Второе условие — главный предохранитель: без него две разные новости склеились бы только потому, что обе «про АПК». Плюс ограничение по времени (окно ~5 дней) и по размеру кластера.

4. **Группа становится событием при ≥3 разных источниках.** Это порог значимости (`EVENT_MIN_SOURCES`): один-два источника — слабое событие, оно сохраняется как `ignored_weak`, но в интерфейсе не показывается.

5. **Достраиваем карточку события:**
   - **заголовок** — самый репрезентативный из источников (ближе всего к «центру» кластера);
   - **резюме** — 2–3 самых показательных предложения из разных публикаций (extractive, без генерации текста);
   - **σ (достоверность)** — выше, если источники тесно связаны между собой;
   - **грани** — темы / регионы / продукты;
   - **оценка по ролям** (фермер / трейдер / …): позитив / риск / следить.

6. **Сохраняем и пересобираем связи.** Событие пишется в БД со списком источников; затем отдельно строится граф связей между событиями и сюжеты-цепочки.

**Главный принцип одной фразой:** объединяем новости в событие, только когда они и по смыслу близки, и говорят про один конкретный инфоповод (общий якорь), в пределах нескольких дней — и показываем событие, лишь когда о нём написали минимум три источника.

> ⚠️ Нюанс на будущее: сейчас новая новость доклеивается к уже существующему событию только если её пересчитанный ключ совпадёт со старым; «догоняющая» новость, пришедшая позже, чаще создаёт отдельное событие. При переезде на внешний RAG это логично улучшить — искать ближайшее существующее событие по вектору и дописывать источник к нему.

Полное техническое описание алгоритма (параметры, формулы, SQL) — в [`docs/EVENTS_AND_STORIES.md`](docs/EVENTS_AND_STORIES.md).

## Постоянные таблицы

Worker создаёт таблицы автоматически в схеме `EVENTS_SCHEMA`, по умолчанию:

```text
harvester_news.events
harvester_news.event_sources
harvester_news.event_impacts
harvester_news.event_job_state
```

Файл ручной инициализации также лежит здесь:

```bash
backend/sql/001_events_schema.sql
```

## Настройка .env

```bash
cp .env.example .env
nano .env
```

Минимум:

```env
PG_CONNINFO="dbname=DBNAME user=USER password=PASSWORD host=DB_HOST port=5432"
NEWS_TABLE=news_list
EVENTS_SCHEMA=harvester_news
```

Если таблица `news_list` лежит не в `public`, можно указать:

```env
NEWS_SCHEMA=имя_схемы
```

Если `NEWS_SCHEMA` не указан, backend сам ищет схему таблицы `news_list` через `information_schema`.

## Настройки semantic-RAG worker

```env
EVENT_ANALYSIS_MODE=semantic-rag
EVENT_CONTEXT_SOURCES_LIMIT=12

SEMANTIC_RAG_ENABLED=true
SEMANTIC_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
SEMANTIC_DEVICE=cpu
SEMANTIC_BATCH_SIZE=32
SEMANTIC_CLUSTER_WINDOW_DAYS=5
SEMANTIC_CLUSTER_MIN_COSINE=0.66
SEMANTIC_CLUSTER_STRONG_COSINE=0.82
SEMANTIC_MIN_TOKEN_OVERLAP=0.10
SEMANTIC_MAX_CLUSTER_SIZE=10

EVENT_WORKER_INTERVAL_SECONDS=300
EVENT_WORKER_BATCH_SIZE=120
EVENT_WORKER_FETCH_LIMIT=500
EVENT_WORKER_LOOKBACK_DAYS=30
EVENT_WORKER_PROCESS_ALL=false
```

Что означают настройки:

- `SEMANTIC_CLUSTER_WINDOW_DAYS` — насколько широко по датам можно склеивать публикации в одно событие.
- `SEMANTIC_CLUSTER_MIN_COSINE` — минимальная семантическая близость для кандидата в склейку.
- `SEMANTIC_CLUSTER_STRONG_COSINE` — порог, при котором публикации считаются почти дублями даже при слабом совпадении тегов.
- `SEMANTIC_MIN_TOKEN_OVERLAP` — страховка от овермерджа: кроме похожести embeddings нужны общие смысловые токены/теги.
- `SEMANTIC_MAX_CLUSTER_SIZE` — защита от слишком больших корявых кластеров.
- `EVENT_CONTEXT_SOURCES_LIMIT` — сколько источников сохранять в технический контекст анализа.
- `EVENT_WORKER_INTERVAL_SECONDS` — как часто worker ищет новые новости.
- `EVENT_WORKER_PROCESS_ALL=true` — первичный прогон всей базы; после него лучше вернуть `false`.

Если нужно вернуться к старому быстрому алгоритму без embeddings:

```env
EVENT_ANALYSIS_MODE=offline-rag
SEMANTIC_RAG_ENABLED=false
```

## Запуск

```bash
docker compose up -d --build backend frontend events-worker
```

Разовый прогон worker:

```bash
docker compose run --rm events-worker python -m app.workers.events_worker --once
```

Логи worker:

```bash
docker logs -f test-news-events-worker
```

Проверка:

```bash
curl http://127.0.0.1:6885/api/v1/news/events/stats
curl 'http://127.0.0.1:6885/api/v1/news/events?limit=6'
```


## Пересборка событий после смены RAG-алгоритма

Если в таблицах уже лежат старые корявые события, их нужно пересобрать:

```bash
docker compose exec backend python - <<'PY'
import asyncio
from psycopg import sql
from app.config import settings
from app.db.db_ext import open_pool, close_pool, get_conn
from app.services.event_tables import ensure_event_schema

async def main():
    await open_pool()
    await ensure_event_schema()
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                sql.SQL("TRUNCATE {}.event_impacts, {}.event_sources, {}.events RESTART IDENTITY CASCADE").format(
                    sql.Identifier(settings.events_schema),
                    sql.Identifier(settings.events_schema),
                    sql.Identifier(settings.events_schema),
                )
            )
        await conn.commit()
    await close_pool()

asyncio.run(main())
PY

docker compose run --rm events-worker python -m app.workers.events_worker --once
```

При первом запуске semantic-RAG модель может скачиваться несколько минут. Дальше она будет браться из docker volume.

## API событий

```bash
curl 'http://127.0.0.1:6885/api/v1/news/events?limit=6'
curl 'http://127.0.0.1:6885/api/v1/news/events?tag=пшеница&role=trader'
```

Событие содержит:

- `title`, `summary`;
- `news_count`;
- `sources_count`;
- `sigma`;
- `tags`, `regions`, `products`;
- `sources` — исходные публикации;
- `impacts` — влияние на роли: фермер, переработчик, трейдер, экспортёр, агрохолдинг.

## Порты

Проект не занимает `80`, `443`, `8000`, `5173`, `5432` и другие системные/занятые порты.
Наружу проброшен только локальный порт:

```text
127.0.0.1:6885 -> frontend:8080
```

Backend доступен только внутри docker-сети и напрямую на хост не публикуется.

## Nginx на сервере

Добавьте в server block вашего домена:

```nginx
location = /test_news {
    return 301 /test_news/;
}

location /test_news/ {
    proxy_pass http://127.0.0.1:6885/;
    proxy_http_version 1.1;
    proxy_redirect off;

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Prefix /test_news;

    proxy_connect_timeout 60s;
    proxy_send_timeout 300s;
    proxy_read_timeout 300s;
    client_max_body_size 256M;
}
```

Перезагрузка nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## Обновление проекта

```bash
docker compose down
docker compose up -d --build backend frontend events-worker
```

## Диагностика

```bash
docker ps | grep test-news
docker logs -f test-news-backend
docker logs -f test-news-events-worker
docker logs -f test-news-frontend
curl http://127.0.0.1:6885/api/v1/news/debug/db
curl http://127.0.0.1:6885/api/v1/news/events/stats
```

Если backend пишет, что `news_list` не найдена, проверьте `dbname`, пользователя и схему таблицы.

## Фильтр качества новостей

Backend и worker не учитывают технические/пустые записи из `news_list`:

- пустой `text`;
- заголовки-заглушки вроде `Без заголовка`;
- записи, где текст почти полностью состоит из ссылки/перехода;
- короткие сервисные посты вида `Telegram | MAX`, `Дашборд KSM` без содержательного текста.

Фильтр применяется глобально: лента, события, timeline, meta, featured, top-read и страница новости.

## Пересборка событий semantic-RAG

Если менялись настройки склейки событий или в интерфейсе появились криво склеенные события, пересоберите слой событий:

```bash
docker compose run --rm events-worker python -m app.workers.events_worker --reset --once
```

Новый semantic-RAG специально исключает дайджесты/подборки из worker-склейки и требует совпадения заголовочных якорей, поэтому события станут более дробными, но заметно чище.

## Ускорение главной страницы

В этой версии главная страница загружается быстрее:

- вместо 6 отдельных HTTP-запросов frontend использует один агрегированный endpoint `/api/v1/news/home`;
- backend параллельно собирает новости, события, график, meta, featured и top-read;
- тяжёлые справочники (`/meta`), график активности, top-read, featured, события и первый экран новостей кешируются в памяти backend на короткий TTL;
- пагинация «Показать ещё» грузит только следующий кусок новостей, без повторной загрузки графика и событий;
- на страницах события/новости больше не запускается лишняя загрузка главной страницы.

Проверка:

```bash
curl 'http://127.0.0.1:6885/api/v1/news/home?limit=20&offset=0'
```

## Быстрый старт главной страницы

Главная страница теперь грузится в два этапа:

1. Первый быстрый запрос `/api/v1/news/home/fast-week` возвращает только новости и события за последнюю доступную неделю. Это позволяет почти сразу показать полезный первый экран.
2. Второй фоновый запрос `/api/v1/news/home` подгружает полную историю, график активности, справочники, featured и блок «читают сейчас».

Пока идёт фоновая загрузка, пользователь видит баннер «Быстрый старт». Масштаб графика и фильтрация по периоду продолжают работать как раньше: список ограничивается датами только после клика по столбику графика или ручного выбора периода.


## График активности включён

График активности снова загружается на главной странице:

```text
/api/v1/news/home -> включает timeline
/api/v1/news/timeline -> отдельный endpoint сохранён
```

Первый экран по-прежнему использует быстрый недельный режим, а полная главная догружается в фоне.

## Важно: сборка semantic-RAG без CUDA

Backend и worker собираются разными Dockerfile:

- `backend/Dockerfile` — лёгкий API без `torch` и `sentence-transformers`;
- `backend/Dockerfile.worker` — semantic-RAG worker с CPU-only `torch`.

Это нужно, чтобы сервер не скачивал CUDA-пакеты NVIDIA на несколько гигабайт.

Если ранее сборка упала с `No space left on device`, очистите Docker cache:

```bash
docker compose down || true
docker builder prune -af
docker image prune -af
docker container prune -f
df -h
```

После этого запускайте:

```bash
docker compose up -d --build backend frontend events-worker
```

## Оптимизация подсчётов на главной

В этой версии справочники тегов/регионов/продуктов, источники, общее количество новостей и общее количество событий считаются SQL-запросами на стороне PostgreSQL. Backend больше не выгружает всю `news_list` в Python только ради подсчёта facet'ов.

Опциональные индексы для большой базы лежат в:

```bash
backend/sql/002_optional_perf_indexes.sql
```

Их можно применить вручную в PostgreSQL, если таблица `news_list` большая.

## Пересборка событий за год

Если график показывает период, а блок событий пустой, значит события для этого периода ещё не были рассчитаны worker-ом. Для полной первичной пересборки используйте:

```bash
docker-compose run --rm events-worker python -m app.workers.events_worker --reset --once --drain
```

Worker обрабатывает новости пакетами до тех пор, пока в выбранном окне не останется необработанных публикаций. По умолчанию окно берётся относительно последней даты в `news_list`, а не системной даты сервера.

## Изменения в версии topics-fast

- Основная фильтрация и график переведены с общего набора тегов на поле `topics`.
- Сырые теги `topics` больше не используются в первом экране и timeline, чтобы снизить нагрузку на PostgreSQL и упростить визуал.
- Новости без `link_photo` теперь отображаются без блока фотографии; на странице новости большой фото-блок тоже скрывается, если фото нет.
- Счётчики фильтров считаются по подготовленным событиям и темам, а не по полной сырой теговой каше.

## Быстрая разметка тем

Чтобы график и фильтры не разбирали `news_list.topics` при каждом запросе, backend создаёт нормализованную таблицу:

```text
harvester_news.news_topic_marks
```

Она заполняется worker-ом автоматически. После первого деплоя или после полной пересборки данных можно принудительно построить индекс тем:

```bash
docker-compose run --rm events-worker python -m app.workers.events_worker --sync-topics --reset-topics --drain
```

После этого график `/api/v1/news/timeline`, фильтр по темам и `/api/v1/news/meta` используют `news_topic_marks`, а не тяжёлый JSONB-scan по всей `news_list`.

## Fix: события при выборе дня/недели/месяца

В этой версии фильтр событий по выбранному периоду стал мягче и надёжнее:

- событие ищется по `events.date_from/date_to`;
- если старые события имеют неполные даты, дополнительно проверяются даты источников `event_sources.news_date`;
- если и там дата отсутствует, дата подтягивается через `news_list` по `event_sources.news_id` и `main_news_id`;
- фильтр по темам для событий теперь смотрит не только `events.topics`, но и нормализованную таблицу `harvester_news.news_topic_marks` у новостей-источников;
- если строгие фильтры периода ничего не нашли, backend показывает события выбранного периода без дополнительных facet-фильтров, чтобы пользователь не видел ложное «событий нет».

Frontend получил анимацию обновления событий при клике по столбцу графика.

## Frontend static Dockerfile

В production frontend не собирается через npm внутри Docker. Контейнер `frontend` копирует уже готовую папку `frontend/dist` в nginx. Поэтому `frontend/.dockerignore` не должен исключать `dist`.

Проверка:

```bash
cat frontend/Dockerfile
cat frontend/.dockerignore
```

В `frontend/Dockerfile` не должно быть `npm run build` или `vite build`.

## Быстрый график через дневные агрегаты

Если `news_topic_marks` уже заполнена, но `/api/v1/news/timeline` всё ещё грузится долго, нужно построить готовые дневные агрегаты:

```text
harvester_news.topic_daily_stats
harvester_news.topic_daily_totals
```

Они хранят уже посчитанные строки для графика:

```text
дата + тема + количество
дата + всего новостей
```

Разовая команда после деплоя:

```bash
docker-compose run --rm events-worker python -m app.workers.events_worker --sync-topic-stats
```

После этого главный график без активных фильтров читает маленькие агрегированные таблицы, а не делает JOIN/GROUP BY по `news_list` и `news_topic_marks` на каждый заход.

Обычный `events-worker` при появлении новых новостей обновляет агрегаты только для затронутых дат.


## Пересборка событий с порогом источников

В этой версии для показа используются только активные события, у которых `sources_count >= EVENT_MIN_SOURCES` — по умолчанию `3`.
Слабые события сохраняются со статусом `ignored_weak`, чтобы worker не обрабатывал те же новости повторно, но API и главный блок их не показывают.

Полная очистка и пересборка событий:

```bash
docker-compose stop events-worker
docker-compose run --rm events-worker python -m app.workers.events_worker --reset --once --drain
docker-compose run --rm events-worker python -m app.workers.events_worker --sync-topic-stats
docker-compose up -d events-worker
```

Проверка:

```bash
curl https://your-domain.example/test_news/api/v1/news/events/stats
curl "https://your-domain.example/test_news/api/v1/news/events?limit=5"
```


### Пересборка событий с защитой от повторной обработки

В этой версии добавлена таблица `harvester_news.event_news_state`. Она помечает все новости,
которые event-worker уже просмотрел, даже если строка не стала источником события
(например, дайджест или слабая одиночная публикация). Поэтому `--drain` должен доходить до
`fetched: 0`, а не гонять один и тот же batch по 300 новостей.

Полная пересборка событий:

```bash
cd /home/sammy/harvester_news_project
docker-compose stop events-worker
docker-compose run --rm events-worker python -m app.workers.events_worker --reset --once --drain
docker-compose run --rm events-worker python -m app.workers.events_worker --sync-topic-stats
docker-compose up -d events-worker
```

В логах нормальный прогресс теперь выглядит так:

```text
run result: {..., 'fetched': 300, 'news_seen': 300, 'news_clustered': ..., 'news_skipped': ...}
```

Завершение пересборки:

```text
run result: {..., 'fetched': 0, 'groups': 0, 'events_upserted': 0}
```

## Пересборка настоящих тем

После деплоя один раз пересоберите индекс тем и дневные агрегаты:

```bash
docker-compose stop events-worker

docker-compose run --rm events-worker python -m app.workers.events_worker --sync-topics --reset-topics --drain

docker-compose run --rm events-worker python -m app.workers.events_worker --sync-topic-stats

docker-compose up -d events-worker
```

При необходимости список тем можно расширить в `.env`:

```env```
