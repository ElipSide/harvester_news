-- Очистить слой событий и пересобрать заново.
-- После применения запустите:
--   docker compose run --rm events-worker python -m app.workers.events_worker --once

TRUNCATE TABLE harvester_news.event_impacts RESTART IDENTITY CASCADE;
TRUNCATE TABLE harvester_news.event_sources RESTART IDENTITY CASCADE;
TRUNCATE TABLE harvester_news.events RESTART IDENTITY CASCADE;
TRUNCATE TABLE harvester_news.event_news_state RESTART IDENTITY CASCADE;
DELETE FROM harvester_news.event_job_state;
