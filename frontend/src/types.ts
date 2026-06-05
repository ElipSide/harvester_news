export type FacetItem = {
  name: string;
  count: number;
};

export type NewsItem = {
  id: number;
  id_message: number;
  date: string | null;
  title: string;
  text: string;
  summary: string;
  tag: unknown;
  link_site: string | null;
  source: string | null;
  link_photo: string | null;
  customer: string | null;
  object: unknown;
  extra_tag: unknown;
  views: number;
  subscribers: number;
  regions: string[];
  products: string[];
  topics: string[];
  tags: string[];
};

export type NewsListResponse = {
  total: number;
  limit: number;
  offset: number;
  items: NewsItem[];
};

export type NewsMetaResponse = {
  total: number;
  news_total?: number;
  events_total?: number;
  topics: FacetItem[];
  regions: FacetItem[];
  products: FacetItem[];
  tags: FacetItem[];
  sources: FacetItem[];
  customers: FacetItem[];
};

export type TimelineDay = {
  date: string;
  total: number;
  topics: Record<string, number>;
  related: FacetItem[];
};

export type TimelineResponse = {
  days: number;
  date_from: string;
  date_to: string;
  total: number;
  avg_per_day: number;
  topics: FacetItem[];
  items: TimelineDay[];
};

export type EventSource = {
  id: number;
  title: string;
  source: string | null;
  date: string | null;
  link_site: string | null;
};

export type EventRoleImpact = {
  role: 'farmer' | 'processor' | 'trader' | 'agroholding' | 'exporter';
  label: string;
  impact: 'positive' | 'negative' | 'neutral' | 'watch';
  summary: string;
  action_hint: string;
};

export type EventItem = {
  id: string;
  title: string;
  summary: string;
  date_from: string | null;
  date_to: string | null;
  news_count: number;
  sources_count: number;
  sigma: number;
  views: number;
  tags: string[];
  topics: string[];
  regions: string[];
  products: string[];
  impacts: EventRoleImpact[];
  sources: EventSource[];
  main_news_id: number | null;
};

export type EventListResponse = {
  total: number;
  limit: number;
  offset: number;
  items: EventItem[];
};

export type EventGraphItem = {
  id: string;
  date_from: string | null;
  date_to: string | null;
  topics: string[];
  regions: string[];
  products: string[];
};

export type EventGraphResponse = {
  total: number;
  items: EventGraphItem[];
};

// ─── Story timeline (эго-граф сюжета вокруг события) ─────────────────────────
// Канал ветки: P=продукт, G=регион, T=тема, A=игрок (null у фокуса).
export type StoryChannel = 'P' | 'G' | 'T' | 'A' | null;

export type StoryNode = {
  id: string;
  date: string;            // ISO YYYY-MM-DD
  title: string;
  sigma: number;
  ch: StoryChannel;        // общий канал с фокусом
  lab: string | null;      // общая сущность (название продукта/региона/темы)
  color: string;
  main_news_id: number | null;
  role: 'focus' | 'sprev' | 'snext' | 'facet';
  story: boolean;          // ветка сюжетной цепочки (рисуется толще)
};

export type StoryResponse = {
  focus: StoryNode | null;
  story: { name: string; color: string; count: number } | null;
  nodes: StoryNode[];      // только ветки; фокус отдельно
};

// ─── Полный граф событий (explorer на странице чтения, порт harvester_tree.html) ───
export type FullGraphNode = {
  id: number;
  date: string;            // ISO YYYY-MM-DD
  sg: number;              // sigma
  ti: string;              // title
  dek: string;             // summary
  src: number;             // sources_count
  main_news_id: number | null;
  p: string[];             // продукты
  g: string[];             // регионы
  t: string[];             // темы (без игроков)
  a: string[];             // игроки (компании/ведомства/персоны)
  s: number[];             // id сюжетов, в которые входит событие
};
export type FullGraphEdge = [number, number, number, string, string | null]; // from,to,weight,channel,lab
export type FullGraphStory = { id: number; name: string; color: string; ev: number[] };
export type FullGraphResponse = {
  nodes: FullGraphNode[];
  edges: FullGraphEdge[];
  stories: FullGraphStory[];
  focus_event_id: number | null;
  focus_title?: string;
  focus_article?: string;
};


export type HomeResponse = {
  news: NewsListResponse;
  timeline: TimelineResponse | null;
  events: EventListResponse;
  meta: NewsMetaResponse | null;
  featured: NewsItem[];
  top_read: NewsItem[];
  mode?: 'full' | 'fast_week' | 'initial';
  initial_date_from?: string;
  initial_date_to?: string;
};

export type HomeBackgroundResponse = {
  timeline: TimelineResponse;
  meta: NewsMetaResponse;
  featured: NewsItem[];
  top_read: NewsItem[];
};

export type Filters = {
  q: string;
  topics: string[];
  tags: string[];
  region: string | null;
  product: string | null;
  source: string | null;
  period: 'today' | 'week' | 'month' | 'quarter' | null;
  dateFrom: string | null;
  dateTo: string | null;
  hasPhoto: boolean | null;
  sort: 'date_desc' | 'date_asc' | 'views_desc' | 'views_asc';
  role: 'farmer' | 'processor' | 'trader' | 'agroholding' | 'exporter' | null;
};
