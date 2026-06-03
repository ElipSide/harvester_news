import type {
  EventGraphResponse,
  EventListResponse,
  EventRoleImpact,
  EventSource,
  Filters,
  FullGraphResponse,
  HomeBackgroundResponse,
  HomeResponse,
  NewsItem,
  NewsListResponse,
  NewsMetaResponse,
  StoryResponse,
  TimelineResponse,
} from '../types';

const normalizeBaseUrl = (value: string) => (value.endsWith('/') ? value.slice(0, -1) : value);
const APP_BASE_URL = normalizeBaseUrl(import.meta.env.BASE_URL || '/');
const API_URL = import.meta.env.VITE_API_URL || `${APP_BASE_URL}/api/v1`;

let activeController: AbortController | null = null;

function beginRequest(): AbortSignal {
  activeController?.abort();
  activeController = new AbortController();
  return activeController.signal;
}

async function request<T>(path: string, params?: URLSearchParams, signal?: AbortSignal): Promise<T> {
  const url = `${API_URL}${path}${params?.toString() ? `?${params}` : ''}`;
  const response = await fetch(url, { signal });
  if (!response.ok) {
    const details = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${details}`);
  }
  return response.json() as Promise<T>;
}

export function buildNewsParams(
  filters: Filters,
  limit = 20,
  offset = 0,
  options?: { includeTotal?: boolean },
): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.q.trim()) params.set('q', filters.q.trim());
  filters.topics.forEach((topic) => params.append('topic', topic));
  if (filters.region) params.append('region', filters.region);
  if (filters.product) params.append('product', filters.product);
  if (filters.source) params.set('source', filters.source);
  if (filters.dateFrom && filters.dateTo) {
    params.set('date_from', filters.dateFrom);
    params.set('date_to', filters.dateTo);
  } else if (filters.period) {
    params.set('period', filters.period);
  }
  if (filters.hasPhoto !== null) params.set('has_photo', String(filters.hasPhoto));
  params.set('sort', filters.sort);
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  if (options?.includeTotal === false) params.set('include_total', 'false');
  return params;
}

export function buildHomeParams(filters: Filters, limit = 20, offset = 0): URLSearchParams {
  const params = buildNewsParams(filters, limit, offset);
  if (filters.role) params.set('role', filters.role);
  return params;
}

export function buildEventParams(filters: Filters, limit = 6, offset = 0): URLSearchParams {
  const params = buildNewsParams(filters, limit, offset);
  params.delete('has_photo');
  params.delete('sort');
  if (filters.role) params.set('role', filters.role);
  return params;
}

function buildBackgroundParams(filters: Filters): URLSearchParams {
  const params = new URLSearchParams();
  filters.topics.forEach((topic) => params.append('topic', topic));
  if (filters.region) params.append('region', filters.region);
  if (filters.product) params.append('product', filters.product);
  if (filters.source) params.set('source', filters.source);
  return params;
}

export const api = {
  getHome(filters: Filters, limit = 20, offset = 0) {
    return request<HomeResponse>('/news/home', buildHomeParams(filters, limit, offset));
  },
  getHomeInitial(filters: Filters, signal?: AbortSignal) {
    const params = buildHomeParams(filters, 5, 0);
    params.delete('period');
    params.delete('date_from');
    params.delete('date_to');
    params.delete('limit');
    params.delete('offset');
    return request<HomeResponse>('/news/home/initial', params, signal);
  },
  getHomeBackground(filters: Filters, signal?: AbortSignal) {
    return request<HomeBackgroundResponse>('/news/home/background', buildBackgroundParams(filters), signal);
  },
  getHomeFastWeek(filters: Filters, limit = 20, offset = 0) {
    const params = buildHomeParams(filters, limit, offset);
    params.delete('period');
    params.delete('date_from');
    params.delete('date_to');
    return request<HomeResponse>('/news/home/fast-week', params);
  },
  getEvents(filters: Filters, limit = 6, offset = 0, signal?: AbortSignal, sort?: 'date_desc') {
    const params = buildEventParams(filters, limit, offset);
    if (sort) params.set('sort', sort);
    return request<EventListResponse>('/news/events', params, signal);
  },
  getEventsGraph(filters: Filters, limit = 1000, signal?: AbortSignal) {
    const params = buildEventParams(filters, limit, 0);
    // No default date filter — graph shows all active events, consistent with the counter.
    // When a timeline bucket is selected, App.tsx passes explicit dateFrom/dateTo.
    return request<EventGraphResponse>('/news/events/graph', params, signal);
  },
  getNews(filters: Filters, limit = 20, offset = 0, options?: { includeTotal?: boolean }, signal?: AbortSignal) {
    return request<NewsListResponse>(
      '/news',
      buildNewsParams(filters, limit, offset, options),
      signal,
    );
  },
  getNewsById(id: number) {
    return request<NewsItem>(`/news/${id}`);
  },
  getSimilarNews(id: number, limit = 3) {
    const params = new URLSearchParams({ limit: String(limit) });
    return request<NewsItem[]>(`/news/${id}/similar`, params);
  },
  getNewsStory(id: number, signal?: AbortSignal) {
    return request<StoryResponse>(`/news/${id}/story`, undefined, signal);
  },
  getEventsFullGraph(focusNewsId?: number, signal?: AbortSignal) {
    const params = new URLSearchParams();
    if (focusNewsId != null) params.set('focus_news_id', String(focusNewsId));
    return request<FullGraphResponse>('/news/events/full_graph', params, signal);
  },
  getEventDetail(eventId: number, signal?: AbortSignal) {
    return request<{ sources: EventSource[]; impacts: EventRoleImpact[] }>(`/news/events/${eventId}/detail`, undefined, signal);
  },
  getFeatured() {
    const params = new URLSearchParams({ limit: '3' });
    return request<NewsItem[]>('/news/featured', params);
  },
  getTopRead() {
    const params = new URLSearchParams({ limit: '5' });
    return request<NewsItem[]>('/news/top-read', params);
  },
  getTimeline(filters: Filters) {
    const params = new URLSearchParams({ days: '365' });
    filters.topics.forEach((topic) => params.append('topic', topic));
    if (filters.region) params.append('region', filters.region);
    if (filters.product) params.append('product', filters.product);
    if (filters.source) params.set('source', filters.source);
    return request<TimelineResponse>('/news/timeline', params);
  },
  getMeta() {
    return request<NewsMetaResponse>('/news/meta');
  },
};

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

export { beginRequest };
