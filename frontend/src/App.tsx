import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, beginRequest, isAbortError } from "./api/client";
import { useDebouncedValue } from "./hooks/useDebouncedValue";
import { EventIntelligence, EventsBlock } from "./components/EventIntelligence";
import { Featured } from "./components/Featured";
import { FilterBar } from "./components/FilterBar";
import { FilterDrawer } from "./components/FilterDrawer";
import { NewsList } from "./components/NewsList";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
const NewsDetailPage = lazy(() =>
  import("./pages/NewsDetailPage").then((module) => ({ default: module.NewsDetailPage })),
);
import type {
  EventGraphItem,
  EventItem,
  Filters,
  HomeResponse,
  NewsItem,
  NewsMetaResponse,
  TimelineResponse,
} from "./types";

const APP_BASE_PATH = (import.meta.env.BASE_URL || "/").replace(/\/$/, "");

function stripBasePath(pathname: string): string {
  if (
    APP_BASE_PATH &&
    APP_BASE_PATH !== "/" &&
    pathname.startsWith(APP_BASE_PATH)
  ) {
    const next = pathname.slice(APP_BASE_PATH.length) || "/";
    return next.startsWith("/") ? next : `/${next}`;
  }
  return pathname || "/";
}

function withBasePath(pathname: string): string {
  const next = pathname.startsWith("/") ? pathname : `/${pathname}`;
  if (!APP_BASE_PATH || APP_BASE_PATH === "/") return next;
  return `${APP_BASE_PATH}${next === "/" ? "/" : next}`;
}

const defaultFilters: Filters = {
  q: "",
  topics: [],
  tags: [],
  region: null,
  product: null,
  source: null,
  period: null,
  dateFrom: null,
  dateTo: null,
  hasPhoto: null,
  sort: "date_desc",
  role: null,
};

function getNewsIdFromPath(pathname: string): number | null {
  const match = pathname.match(/^\/news\/(\d+)\/?$/);
  return match ? Number(match[1]) : null;
}

function formatSelectedPeriodLabel(
  dateFrom: string | null,
  dateTo: string | null,
): string | null {
  if (!dateFrom || !dateTo) return null;
  const from = new Date(`${dateFrom}T00:00:00`);
  const to = new Date(`${dateTo}T00:00:00`);
  const month = [
    "янв",
    "фев",
    "мар",
    "апр",
    "май",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
  ];
  if (dateFrom === dateTo) return `${from.getDate()} ${month[from.getMonth()]}`;
  if (from.getMonth() === to.getMonth())
    return `${from.getDate()}–${to.getDate()} ${month[to.getMonth()]}`;
  return `${from.getDate()} ${month[from.getMonth()]} – ${to.getDate()} ${month[to.getMonth()]}`;
}

export default function App() {
  const [path, setPath] = useState(
    stripBasePath(window.location.pathname || "/"),
  );
  const [filters, setFilters] = useState<Filters>(defaultFilters);
  const debouncedQ = useDebouncedValue(filters.q, 400);
  const apiFilters = useMemo(
    () => ({ ...filters, q: debouncedQ }),
    [filters, debouncedQ],
  );
  const [meta, setMeta] = useState<NewsMetaResponse | null>(null);
  const [items, setItems] = useState<NewsItem[]>([]);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [graphEvents, setGraphEvents] = useState<EventGraphItem[]>([]);
  const [eventsTotal, setEventsTotal] = useState(0);
  const [eventsPageItems, setEventsPageItems] = useState<EventItem[]>([]);
  const [eventsPageTotal, setEventsPageTotal] = useState(0);
  const [eventsPageOffset, setEventsPageOffset] = useState(0);
  const [eventsPageLoading, setEventsPageLoading] = useState(false);
  const [featured, setFeatured] = useState<NewsItem[]>([]);
  const [topRead, setTopRead] = useState<NewsItem[]>([]);
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [total, setTotal] = useState(0);
  const [limit] = useState(20);
  const [offset, setOffset] = useState(0);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [, setBackgroundLoading] = useState(false);
  const [weekPreview, setWeekPreview] = useState(false);
  const [, setWeekPreviewLabel] = useState<string | null>(null);
  const [periodLoadingVisual, setPeriodLoadingVisual] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailItem, setDetailItem] = useState<NewsItem | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const loadSeqRef = useRef(0);
  const returnToListScrollRef = useRef<number | null>(null);
  const skipNextListReloadRef = useRef(false);

  const newsId = useMemo(() => getNewsIdFromPath(path), [path]);
  const isEventsPage = path === "/events" || path === "/events/";
  const selectedPeriodLabel = formatSelectedPeriodLabel(
    filters.dateFrom,
    filters.dateTo,
  );
  const isPeriodSelected = Boolean(filters.dateFrom && filters.dateTo);
  const activeFilterCount = useMemo(() => (
    filters.topics.length
    + (filters.region ? 1 : 0)
    + (filters.product ? 1 : 0)
    + (filters.hasPhoto !== null ? 1 : 0)
    + (filters.source ? 1 : 0)
    + (filters.q.trim() ? 1 : 0)
    + (filters.period ? 1 : 0)
    + (filters.role ? 1 : 0)
    + (filters.dateFrom && filters.dateTo ? 1 : 0)
  ), [filters]);

  const navigate = useCallback((nextPath: string, options?: { scroll?: boolean; replace?: boolean }) => {
    const appPath = nextPath.startsWith("/") ? nextPath : `/${nextPath}`;
    const browserPath = withBasePath(appPath);
    if (window.location.pathname !== browserPath) {
      if (options?.replace) window.history.replaceState({}, "", browserPath);
      else window.history.pushState({}, "", browserPath);
    }
    setPath(appPath);
    if (options?.scroll !== false) {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, []);

  const openNews = useCallback((id: number) => {
    returnToListScrollRef.current = window.scrollY;
    navigate(`/news/${id}`);
  }, [navigate]);

  const backToList = useCallback(() => {
    skipNextListReloadRef.current = true;
    navigate("/", { scroll: false });
  }, [navigate]);

  useEffect(() => {
    const handlePopState = () =>
      setPath(stripBasePath(window.location.pathname || "/"));
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const loadStatic = useCallback(async () => {
    const [metaData, featuredData, topData] = await Promise.all([
      api.getMeta(),
      api.getFeatured(),
      api.getTopRead(),
    ]);
    setMeta(metaData);
    setFeatured(featuredData);
    setTopRead(topData);
  }, []);

  const applyHomePayload = useCallback(
    (homeData: HomeResponse, nextOffset: number) => {
      if (homeData.meta) setMeta(homeData.meta);
      if (homeData.featured?.length) setFeatured(homeData.featured);
      if (homeData.top_read?.length) setTopRead(homeData.top_read);
      if (homeData.timeline) setTimeline(homeData.timeline);
      setItems(homeData.news.items);
      setTotal(homeData.news.total);
      setOffset(nextOffset);
      setEvents(homeData.events.items);
      setEventsTotal(homeData.events.total);
    },
    [],
  );

  const initialFastAllowed = useMemo(() => {
    return !isEventsPage && !newsId && !filters.period && !filters.dateFrom && !filters.dateTo;
  }, [filters.dateFrom, filters.dateTo, filters.period, isEventsPage, newsId]);

  const loadBackgroundVisuals = useCallback(
    async (seq: number, signal: AbortSignal) => {
      setBackgroundLoading(true);
      try {
        // Graph always shows the full event dataset for current time context.
        // Node-level filters (topics/product/region) are applied client-side via
        // state.filters → visibleEvs in EventIntelligence, not via API.
        const graphBaseFilters = { ...apiFilters, topics: [], region: null, product: null, source: null };
        const [homeData, graphData] = await Promise.all([
          api.getHomeBackground(apiFilters, signal),
          api.getEventsGraph(graphBaseFilters, 1000, signal),
        ]);
        if (seq !== loadSeqRef.current) return;
        setMeta(homeData.meta);
        setFeatured(homeData.featured);
        setTopRead(homeData.top_read);
        setTimeline(homeData.timeline);
        setGraphEvents(graphData.items);
      } catch (e) {
        if (isAbortError(e) || seq !== loadSeqRef.current) return;
        setError(e instanceof Error ? e.message : "Ошибка фоновой загрузки графика и тем");
      } finally {
        if (seq === loadSeqRef.current) setBackgroundLoading(false);
      }
    },
    [apiFilters],
  );

  const loadNews = useCallback(
    async (nextOffset = 0, append = false) => {
      const seq = ++loadSeqRef.current;
      const signal = beginRequest();
      setError(null);

      try {
        if (append) {
          setLoading(true);
          const newsData = await api.getNews(
            apiFilters,
            limit,
            nextOffset,
            { includeTotal: false },
            signal,
          );
          if (seq !== loadSeqRef.current) return;
          setItems((prev) => [...prev, ...newsData.items]);
          if (newsData.total >= 0) setTotal(newsData.total);
          setOffset(nextOffset);
          return;
        }

        setLoading(true);
        setWeekPreview(false);
        setWeekPreviewLabel(null);

        if (apiFilters.dateFrom && apiFilters.dateTo) {
          const [newsData, eventsData] = await Promise.all([
            api.getNews(apiFilters, limit, nextOffset, undefined, signal),
            api.getEvents(apiFilters, 30, 0, signal),
          ]);
          if (seq !== loadSeqRef.current) return;
          setItems(newsData.items);
          setTotal(newsData.total);
          setOffset(nextOffset);
          setEvents(eventsData.items);
          setEventsTotal(eventsData.total);
          // graphEvents intentionally NOT updated — graph always shows full dataset,
          // bucket selection filters visually client-side via visibleEvs in EventIntelligence.
          return;
        }

        if (initialFastAllowed && nextOffset === 0) {
          const initialData = await api.getHomeInitial(apiFilters, signal);
          if (seq !== loadSeqRef.current) return;
          applyHomePayload(initialData, 0);
          setWeekPreview(true);
          setWeekPreviewLabel("последние записи");
          setLoading(false);
          void loadBackgroundVisuals(seq, signal).then(() => {
            if (seq === loadSeqRef.current) {
              setWeekPreview(false);
              setWeekPreviewLabel(null);
            }
          });
          return;
        }

        const [newsData, eventsData] = await Promise.all([
          api.getNews(apiFilters, limit, nextOffset, undefined, signal),
          api.getEvents(apiFilters, 5, 0, signal, "date_desc"),
        ]);
        if (seq !== loadSeqRef.current) return;
        setItems(newsData.items);
        setTotal(newsData.total);
        setOffset(nextOffset);
        setEvents(eventsData.items);
        setEventsTotal(eventsData.total);
        void loadBackgroundVisuals(seq, signal);
      } catch (e) {
        if (isAbortError(e) || seq !== loadSeqRef.current) return;
        setError(e instanceof Error ? e.message : "Неизвестная ошибка");
      } finally {
        if (seq === loadSeqRef.current) {
          setLoading(false);
        }
      }
    },
    [applyHomePayload, apiFilters, initialFastAllowed, limit, loadBackgroundVisuals],
  );

  const loadEventsPage = useCallback(
    async (nextOffset = 0, append = false) => {
      setEventsPageLoading(true);
      setError(null);
      try {
        const data = await api.getEvents(apiFilters, 30, nextOffset);
        setEventsPageItems((prev) =>
          append ? [...prev, ...data.items] : data.items,
        );
        setEventsPageTotal(data.total);
        setEventsPageOffset(nextOffset);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Ошибка загрузки событий");
      } finally {
        setEventsPageLoading(false);
      }
    },
    [apiFilters],
  );

  const refreshAll = useCallback(() => {
    if (isEventsPage) {
      loadStatic().catch((e) =>
        setError(
          e instanceof Error ? e.message : "Ошибка загрузки справочников",
        ),
      );
      loadEventsPage(0, false);
      return;
    }
    if (newsId) {
      setDetailLoading(true);
      api
        .getNewsById(newsId)
        .then((item) => {
          setDetailItem(item);
          setDetailError(null);
        })
        .catch((e) =>
          setDetailError(
            e instanceof Error ? e.message : "Ошибка загрузки новости",
          ),
        )
        .finally(() => setDetailLoading(false));
      return;
    }
    loadNews(0, false);
  }, [loadNews, loadStatic, newsId, isEventsPage, loadEventsPage]);

  useEffect(() => {
    if (!isEventsPage) return;
    loadStatic().catch((e) =>
      setError(e instanceof Error ? e.message : "Ошибка загрузки справочников"),
    );
  }, [isEventsPage, loadStatic]);

  useEffect(() => {
    if (isEventsPage || newsId) return;
    if (skipNextListReloadRef.current) {
      skipNextListReloadRef.current = false;
      return;
    }
    loadNews(0, false);
  }, [loadNews, isEventsPage, newsId]);

  useEffect(() => {
    if (!isEventsPage) return;
    loadEventsPage(0, false);
  }, [isEventsPage, loadEventsPage]);

  useEffect(() => {
    if (!newsId) {
      setDetailItem(null);
      setDetailError(null);
      return;
    }
    setDetailLoading(true);
    setDetailError(null);
    api
      .getNewsById(newsId)
      .then(setDetailItem)
      .catch((e) =>
        setDetailError(
          e instanceof Error ? e.message : "Ошибка загрузки новости",
        ),
      )
      .finally(() => setDetailLoading(false));
  }, [newsId]);

  useEffect(() => {
    if (newsId || isEventsPage) return undefined;
    const scrollY = returnToListScrollRef.current;
    if (scrollY === null) return undefined;
    returnToListScrollRef.current = null;

    const restore = () => window.scrollTo({ top: scrollY, behavior: "auto" });
    const frame = window.requestAnimationFrame(() => {
      window.requestAnimationFrame(restore);
    });
    const timerShort = window.setTimeout(restore, 120);
    const timerLong = window.setTimeout(restore, 420);

    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timerShort);
      window.clearTimeout(timerLong);
    };
  }, [newsId, isEventsPage, items.length]);

  const updateFilters = (patch: Partial<Filters>) => {
    setFilters((prev) => {
      const next = { ...prev, ...patch };
      if ("period" in patch) {
        next.dateFrom = null;
        next.dateTo = null;
      }
      return next;
    });
    if (newsId) navigate("/");
  };

  const toggleTopic = (topic: string) => {
    setFilters((prev) => ({
      ...prev,
      topics: prev.topics.includes(topic)
        ? prev.topics.filter((x) => x !== topic)
        : [...prev.topics, topic],
    }));
    if (newsId) navigate("/");
  };

  const toggleTagFilter = (topic: string) => {
    toggleTopic(topic);
  };

  const applyTagFilter = (topic: string) => {
    setFilters((prev) => ({
      ...prev,
      topics: prev.topics.includes(topic) ? prev.topics : [...prev.topics, topic],
      tags: [],
    }));
    navigate("/");
  };

  const focusSearch = () => {
    navigate("/");
    // На мобильном поле поиска скрыто и показывается только по кнопке.
    setSearchOpen((open) => {
      const next = !open;
      if (next) setTimeout(() => searchInputRef.current?.focus(), 60);
      return next;
    });
  };

  const handleTimelineRangeSelect = (
    dateFrom: string,
    dateTo: string,
    _options?: { scroll?: boolean },
  ) => {
    setPeriodLoadingVisual(true);
    setFilters((prev) => ({ ...prev, dateFrom, dateTo, period: null }));
    if (newsId) navigate("/");
  };

  const clearTimelineRange = () => {
    setFilters((prev) => ({ ...prev, dateFrom: null, dateTo: null }));
  };

  useEffect(() => {
    if (!periodLoadingVisual || loading) return undefined;
    const timer = window.setTimeout(() => setPeriodLoadingVisual(false), 420);
    return () => window.clearTimeout(timer);
  }, [loading, periodLoadingVisual]);

  const hasMore = useMemo(
    () => items.length < total && !weekPreview,
    [items.length, total, weekPreview],
  );
  const eventsPageHasMore = useMemo(
    () => eventsPageItems.length < eventsPageTotal,
    [eventsPageItems.length, eventsPageTotal],
  );

  if (isEventsPage) {
    return (
      <>
        <TopBar
          onNavigate={navigate}
          onSearchClick={focusSearch}
          onRefresh={refreshAll}
        />
        <main className="page events-page">
          <div className="events-page-top">
            <button className="back-btn" onClick={() => navigate("/")}>
              ← К ленте
            </button>
            <h1>Все события</h1>
            <p>
              События сгруппированы по датам. Фильтры и выбранный период
              применяются так же, как на главной.
            </p>
          </div>
          <FilterBar
            filters={filters}
            topics={meta?.topics || []}
            tags={meta?.tags || []}
            total={meta?.total || total}
            onToggleTopic={toggleTopic}
            onResetTopics={() => updateFilters({ topics: [] })}
            onToggleTag={toggleTagFilter}
            onOpenFilters={() => setDrawerOpen(true)}
            onChange={updateFilters}
            searchInputRef={searchInputRef}
            searchOpen={searchOpen}
            selectedPeriodLabel={selectedPeriodLabel}
            onClearTimelineRange={clearTimelineRange}
          />
          {error && <div className="error-box">{error}</div>}
          <EventIntelligence
            events={eventsPageItems}
            total={eventsPageTotal}
            loading={eventsPageLoading}
            role={filters.role}
            onOpenNews={openNews}
            onTagClick={applyTagFilter}
            selectedPeriodLabel={selectedPeriodLabel}
            isPeriodSelected={isPeriodSelected}
            onSelectRange={handleTimelineRangeSelect}
            selectedDateFrom={filters.dateFrom}
            selectedDateTo={filters.dateTo}
            onClearRange={clearTimelineRange}
            fullPage
            hasMore={eventsPageHasMore}
            onShowMore={() => loadEventsPage(eventsPageOffset + 30, true)}
            activeTopics={filters.topics}
            activeRegion={filters.region}
            activeProduct={filters.product}
            onGraphTopicToggle={toggleTopic}
            onGraphRegionToggle={(region) => updateFilters({ region: filters.region === region ? null : region })}
            onGraphProductToggle={(product) => updateFilters({ product: filters.product === product ? null : product })}
            onClearGraphFilters={() => updateFilters({ topics: [], region: null, product: null })}
            onOpenFilters={() => setDrawerOpen(true)}
            filterCount={activeFilterCount}
          />
        </main>

        <FilterDrawer
          open={drawerOpen}
          filters={filters}
          regions={meta?.regions || []}
          products={meta?.products || []}
          topics={meta?.topics || []}

          resultCount={eventsPageTotal}
          onClose={() => setDrawerOpen(false)}
          onChange={updateFilters}
          onToggleTopic={toggleTagFilter}
          onReset={() => setFilters(defaultFilters)}
        />
      </>
    );
  }

  if (newsId) {
    return (
      <>
        <TopBar
          onNavigate={navigate}
          onSearchClick={focusSearch}
          onRefresh={refreshAll}
        />
        <Suspense fallback={<main className="page"><div className="news-list-skeleton" aria-label="Загрузка" /></main>}>
          <NewsDetailPage
            item={detailItem}
            loading={detailLoading}
            error={detailError}
            onBack={backToList}
            onTagClick={applyTagFilter}
            onOpenNews={openNews}
          />
        </Suspense>
      </>
    );
  }

  return (
    <>
      <TopBar
        onNavigate={navigate}
        onSearchClick={focusSearch}
        onRefresh={refreshAll}
      />
      <main className="page">
        <div className="page-hd">
          <FilterBar
            filters={filters}
            topics={meta?.topics || []}
            tags={meta?.tags || []}
            total={meta?.total || total}
            onToggleTopic={toggleTopic}
            onResetTopics={() => updateFilters({ topics: [] })}
            onToggleTag={toggleTagFilter}
            onOpenFilters={() => setDrawerOpen(true)}
            onChange={updateFilters}
            searchInputRef={searchInputRef}
            searchOpen={searchOpen}
            selectedPeriodLabel={selectedPeriodLabel}
            onClearTimelineRange={clearTimelineRange}
          />
        </div>

        {error && <div className="error-box">{error}</div>}
        {/* 1. Активность рынка (граф + таймлайн, без карточек событий) */}
        <EventIntelligence
          events={events}
          graphData={graphEvents}
          total={eventsTotal}
          loading={loading || periodLoadingVisual}
          role={filters.role}
          onOpenNews={openNews}
          onTagClick={applyTagFilter}
          selectedPeriodLabel={selectedPeriodLabel}
          isPeriodSelected={isPeriodSelected}
          onOpenAllEvents={() => navigate("/events")}
          onSelectRange={handleTimelineRangeSelect}
          selectedDateFrom={filters.dateFrom}
          selectedDateTo={filters.dateTo}
          onClearRange={clearTimelineRange}
          hideEventsBlock
          activeTopics={filters.topics}
          activeRegion={filters.region}
          activeProduct={filters.product}
          onGraphTopicToggle={toggleTopic}
          onGraphRegionToggle={(region) => updateFilters({ region: filters.region === region ? null : region })}
          onGraphProductToggle={(product) => updateFilters({ product: filters.product === product ? null : product })}
          onClearGraphFilters={() => updateFilters({ topics: [], region: null, product: null })}
          onOpenFilters={() => setDrawerOpen(true)}
          filterCount={activeFilterCount}
        />

        {/* 2. Топ 3 новости */}
        <Featured
          items={featured}
          onOpenNews={openNews}
        />

        {/* 3. События */}
        <EventsBlock
          events={events}
          total={eventsTotal}
          loading={loading || periodLoadingVisual}
          role={filters.role}
          order="desc"
          onOpenAllEvents={() => navigate("/events")}
          onOpenNews={openNews}
          onTagClick={applyTagFilter}
        />

        {/* 4. Новости */}
        <div className="main">
          <div
            className={`news-list-shell ${periodLoadingVisual ? "period-loading" : ""}`}
          >
            {periodLoadingVisual && (
              <div className="news-period-loader" aria-live="polite">
                <div className="news-period-loader-card">
                  <span className="loader-ring" aria-hidden="true" />
                  <div>
                    <div className="loader-title">
                      Подбираю новости за период
                    </div>
                    <div className="loader-sub">
                      {selectedPeriodLabel || "выбранный диапазон"} · применяю
                      остальные фильтры
                    </div>
                  </div>
                </div>
                <div className="loader-bars" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </div>
              </div>
            )}
            {loading && !items.length ? (
              <div
                className="news-list-skeleton"
                aria-label="Загрузка новостей"
              >
                <span />
                <span />
                <span />
              </div>
            ) : null}
            <NewsList
              items={items}
              total={total}
              hasMore={hasMore}
              onShowMore={() => loadNews(offset + limit, true)}
              onOpenNews={openNews}
              onTagClick={applyTagFilter}
            />
          </div>
          <Sidebar
            topRead={topRead}
            onOpenNews={openNews}
          />
        </div>
      </main>

      <FilterDrawer
        open={drawerOpen}
        filters={filters}
        regions={meta?.regions || []}
        products={meta?.products || []}
        topics={meta?.topics || []}
        sources={meta?.sources || []}
        resultCount={total}
        onClose={() => setDrawerOpen(false)}
        onChange={updateFilters}
        onToggleTopic={toggleTagFilter}
        onReset={() => setFilters(defaultFilters)}
      />
    </>
  );
}
