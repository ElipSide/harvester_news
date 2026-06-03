-- Optional performance indexes for production PostgreSQL.
-- Run manually in the target database if news_list is large.
-- These indexes do not change data and can be created concurrently.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_news_list_quality_date
ON news_list (date DESC, id DESC)
WHERE title IS NOT NULL AND text IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_news_list_source_lower
ON news_list (LOWER(source))
WHERE source IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_news_list_views_date
ON news_list (views DESC, date DESC, id DESC);

-- JSONB containment/filtering indexes. Useful for tag/region/product filters.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_news_list_topics_gin ON news_list USING GIN (topics);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_news_list_regions_gin ON news_list USING GIN (regions);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_news_list_products_gin ON news_list USING GIN (products);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_news_list_tag_gin ON news_list USING GIN (tag);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_news_list_extra_tag_gin ON news_list USING GIN (extra_tag);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_news_list_object_gin ON news_list USING GIN (object);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_events_date_to
ON harvester_news.events (date_to DESC, id DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_events_tags_gin ON harvester_news.events USING GIN (tags);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_events_topics_gin ON harvester_news.events USING GIN (topics);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_events_regions_gin ON harvester_news.events USING GIN (regions);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_events_products_gin ON harvester_news.events USING GIN (products);

-- Ускоряет фильтрацию событий по выбранному периоду графика через источники события.
CREATE INDEX IF NOT EXISTS hn_event_sources_event_date_idx
    ON harvester_news.event_sources (event_id, news_date DESC);

-- Нормализованная разметка тем для быстрого графика и фильтрации.
-- Таблица создаётся backend/worker-ом автоматически, индексы здесь продублированы
-- для ручного применения при необходимости.
CREATE INDEX IF NOT EXISTS hn_news_topics_topic_date_idx
ON harvester_news.news_topic_marks (topic_norm, news_date DESC, news_id);

CREATE INDEX IF NOT EXISTS hn_news_topics_date_idx
ON harvester_news.news_topic_marks (news_date DESC, news_id);

CREATE INDEX IF NOT EXISTS hn_news_topics_news_idx
ON harvester_news.news_topic_marks (news_id);

-- Ускоряет выдачу только качественных событий с 3+ источниками.
CREATE INDEX IF NOT EXISTS hn_events_active_sources_date_idx
ON harvester_news.events (sources_count DESC, date_to DESC, id DESC)
WHERE status = 'active';
