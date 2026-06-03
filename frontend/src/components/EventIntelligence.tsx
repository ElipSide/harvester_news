import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import type { EventItem, EventGraphItem, Filters } from '../types';
import { cleanSummary } from '../utils/text';

// ─── Types ────────────────────────────────────────────────────────────────────

type Metric = 'freq' | 'lift';
type Scale  = 'week' | 'month' | 'quarter' | 'year';
type Ax     = 'c' | 'p' | 'g';

type EvRow = {
  c: string[];
  p: string[];
  g: string[];
  dateFrom: string;   // original ISO date for bucket computation
  title: string;
};

type NodePos = {
  x: number; y: number;
  ax: Ax; k: string; n: number; ci: number;
};

type LinkItem = {
  axA: Ax; a: string;
  axB: Ax; b: string;
  freq: number; mv: number;
  pa: NodePos; pb: NodePos;
  d: string; wdt: number;
};

type GraphInfo = {
  topLink: string;
  topLabel: string;
  evCount: number;
  totals: Record<Ax, number>;
};

type State = {
  scale: Scale;
  metric: Metric;
  hideCommon: boolean;
  flow: boolean;
  center: Ax;
  filters: Record<Ax, Set<string>>;
  expanded: Record<Ax, boolean>;
  selectedBuckets: Set<string>;
  settingsOpen: boolean;
};

type Action =
  | { type: 'SET_SCALE'; scale: Scale }
  | { type: 'SET_METRIC'; metric: Metric }
  | { type: 'TOGGLE_HIDE_COMMON' }
  | { type: 'TOGGLE_FLOW' }
  | { type: 'SET_CENTER'; ax: Ax }
  | { type: 'TOGGLE_FILTER'; ax: Ax; k: string }
  | { type: 'REMOVE_FILTER'; ax: Ax; k: string }
  | { type: 'CLEAR_FILTERS' }
  | { type: 'SET_FILTERS'; filters: Record<Ax, Set<string>> }
  | { type: 'TOGGLE_EXPANDED'; ax: Ax }
  | { type: 'TOGGLE_BUCKET'; key: string }
  | { type: 'CLEAR_BUCKETS' }
  | { type: 'OPEN_SETTINGS' }
  | { type: 'CLOSE_SETTINGS' };

// ─── Constants ────────────────────────────────────────────────────────────────

const SCALE_LABELS: Record<Scale, string> = { week: 'неделя', month: 'месяц', quarter: 'квартал', year: 'год' };
const TOPN = 5, TOPN_COMPACT = 3, EXPN = 12;
const topnFor = (compact: boolean) => (compact ? TOPN_COMPACT : TOPN);
const MON = ['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек'];
const MON_CAP = ['Янв','Фев','Мар','Апр','Май','Июн','Июл','Авг','Сен','Окт','Ноя','Дек'];
const AXNAME: Record<Ax, string> = { c: 'тема', p: 'продукт', g: 'география' };
const SVG_NS = 'http://www.w3.org/2000/svg';

// Epoch for week bucket keys (Mon 2020-01-06)
const WEEK_EPOCH = new Date('2020-01-06T00:00:00').getTime();

const PAL: Record<Ax, Record<string, string>> = {
  // Blue/indigo/purple palette for topics and actors — matches handoff design
  c: {
    // Regulation / policy (dark navy)
    'Регулирование':'#3B4A6B','Правительство':'#3B4A6B','Министерство':'#3B4A6B',
    'Политика':'#3B4A6B','Господдержка':'#3B4A6B','Субсидии':'#3B4A6B',
    'Квоты':'#3B4A6B','Пошлины':'#3B4A6B','Льготы':'#3B4A6B','Кредитование':'#3B4A6B',
    'Налоги':'#3B4A6B',
    // Trade / export / market (blue)
    'Экспорт':'#1E4FB0','Импорт':'#1E4FB0','Торговля':'#1E4FB0','Логистика':'#1E4FB0',
    'Санкции':'#1E4FB0','Тендер':'#1E4FB0','Биржа':'#1E4FB0','Лизинг':'#3D6B8A',
    // Analytics / prices (lighter blue)
    'Аналитика':'#2E6FD0','Цена':'#2E6FD0','Тренд':'#2E6FD0','Индекс':'#2E6FD0',
    'Обзор Рынка':'#2E6FD0','Отчет':'#2E6FD0','Динамика Цен':'#2E6FD0','Прогноз Цен':'#2E6FD0',
    // Technology / agronomy (slate blue)
    'Технологии':'#5A6FA8','Мероприятия':'#5B6A8B','Выставка':'#5B6A8B',
    'Удобрения':'#5A6FA8','Беспилотник':'#5A6FA8','Биотехнологии':'#5A6FA8',
    'Ветеринария':'#5A6FA8','Наука':'#5A6FA8','Гербицид':'#5A6FA8','Пестицид':'#5A6FA8',
    'Роботизация':'#5A6FA8','Точное Земледелие':'#5A6FA8','Искусственный Интеллект':'#5A6FA8',
    'Мелиорация':'#5A6FA8','Гис':'#5A6FA8','Фгис':'#5A6FA8','Средства Защиты Растений':'#5A6FA8',
    'Производители удобрений':'#5A6FA8','Производители СЗР':'#5A6FA8',
    // Harvest / season / crop cycle (teal-blue)
    'Урожай':'#3E7CC2','Уборка':'#3E7CC2','Сев':'#3E7CC2','Переработка':'#3E7CC2',
    'Завод':'#3E7CC2','Линия':'#3E7CC2','Намолот':'#3E7CC2','Озимые':'#3E7CC2',
    'Яровые':'#3E7CC2','Посевные Площади':'#3E7CC2','Валовой Сбор':'#3E7CC2',
    // Port / logistics / storage (teal)
    'Порт':'#2F7E8E','Хранение':'#2F7E8E','Терминал':'#2F7E8E','Склад':'#2F7E8E',
    'Перевалка':'#2F7E8E','Элеватор':'#2F7E8E','Автоперевозки':'#2F7E8E',
    // Weather / moisture (cyan)
    'Погода':'#2E8B9E','Переувлажнение':'#2E8B9E','Засуха':'#A1361B',
    // Problems / risks (red)
    'Проблемы':'#A1361B','Неурожай':'#A1361B','Вредитель':'#A1361B','Сорные Растения':'#A1361B',
    // Legal (violet)
    'Суд':'#6B5BA8','Таможня':'#6B5BA8','Иск':'#6B5BA8','Сертификат':'#6B5BA8',
    'Гост':'#7A6BB0','Декларация':'#6B5BA8',
    // Actors / names (purple)
    'Деятели':'#6E5BD6','Трейдеры':'#7A6BB0','Агрохолдинги':'#6E5BD6',
    'Лут':'#6E5BD6','Патрушев':'#6E5BD6','Мишустин':'#6E5BD6','Кондратьев':'#6E5BD6',
    'Томенко':'#6E5BD6','Двойных':'#6E5BD6','Данкверт':'#6E5BD6','Абрамченко':'#6E5BD6',
    'Гордеев':'#6E5BD6','Разин':'#6E5BD6','Кашин':'#6E5BD6',
    // Companies (purple shades)
    'Мираторг':'#6E5BD6','Русагро':'#6E5BD6','Эконива':'#6E5BD6','Черкизово':'#6E5BD6',
    'Продимекс':'#6E5BD6','Пищевые компании':'#9B6BC9',
    'Агроэкспорт':'#7E5AD6','Россельхознадзор':'#7E5AD6','Росспецмаш':'#9B5BBF',
    'Россельхозбанк':'#5E5BD6','Ростсельмаш':'#7A6BB0',
    'Зерновой Союз':'#9B5BBF','Масложировой Союз':'#9B5BBF','РЗС':'#9B5BBF','НСА':'#9B5BBF',
    'Акрон':'#8E5AA8','Еврохим':'#8E5AA8','Фосагро':'#8E5AA8','Уралхим':'#8E5AA8',
    'Эфко':'#9B6BC9','Юг Руси':'#9B6BC9','Макфа':'#9B6BC9','Комос Групп':'#9B6BC9',
    'Щёлково Агрохим':'#7A5BC0','Гап «Ресурс»':'#8466D6',
    'Совэкон':'#7A6BB0','Протеин':'#7A6BB0',
    'Семена':'#3E7CC2',
  },
  p: {
    'Пшеница':'#C9A227','Зерновые':'#B8901F','Масличные':'#7A9E3D','Кукуруза':'#E8B834',
    'Зернобобовые':'#5C8C3A','Подсолнечник':'#D88C2A','Подсолнечное масло':'#7A9E3D',
    'Соя':'#7A9E3D','Масло':'#7A9E3D','Жмых/Шрот':'#A88C5A',
    'Шрот Подсолнечника':'#7A9E3D','Шрот Льна':'#7A9E3D','Шрот Рапсовый':'#7A9E3D',
    'Ячмень':'#A88C1F','Рис':'#C9C027','Рапс':'#7A9E3D','Рапсовое Масло':'#7A9E3D',
    'Лен':'#9EAE6D','Льняное Масло':'#7A9E3D','Горох':'#6C8C4A','Овощи':'#5C9E5A',
    'Крупяные':'#B8A040','Мука/Крупа':'#C9B060','Мука':'#B8901F','Крупы':'#B8A040',
    'Ягоды':'#A1361B','Фрукты':'#C26B3C','Кофе':'#6B4A2A',
    'Грибы':'#8E7A5A','Чай':'#6B8E5A','Бахчевые':'#9E8E3D','Чечевица':'#8C7A3A',
    'Орехи':'#8E6B4A','Удобрения':'#7A8A6D','Гречиха':'#B8901F','Рожь':'#B8901F',
    'Сахар':'#C98A27','Сахарная Свекла':'#C98A27','Свекла':'#5C9E5A',
    'Картофель':'#5C9E5A','Горчица':'#7A9E3D','Нут':'#5C8C3A','Люцерна':'#5C8C3A',
    'Бобы':'#5C8C3A','Фасоль':'#5C8C3A','Гибриды':'#9E8E3D','Селекция':'#9E8E3D',
    'Соевое Масло':'#7A9E3D','Оливковое Масло':'#7A9E3D','Просо':'#B8901F',
    'Сорго':'#B8901F','Овес':'#B8901F','Кукурузные Хлопья':'#B8901F',
    'Овсяная Крупа':'#B8901F','Ячневая Крупа':'#B8901F',
    'Томат':'#5C9E5A','Огурец':'#5C9E5A','Капуста':'#5C9E5A','Морковь':'#5C9E5A',
    'Лук':'#5C9E5A','Шпинат':'#5C9E5A','Петрушка':'#5C9E5A','Укроп':'#5C9E5A',
    'Дыня':'#5C9E5A','Виноград':'#C26B3C','Вишня':'#C26B3C','Черешня':'#C26B3C',
    'Слива':'#C26B3C','Калина':'#C26B3C','Гранат':'#C26B3C',
    'Вешенка':'#8E7A5A','Арахис':'#8E6B4A','Кунжут':'#7A9E3D','Рыжик':'#7A9E3D',
  },
  g: {
    // Russia overall + major global regions (distinct accent colors)
    'Россия':'#4A6B9E','Азия':'#C26B3C','Европа':'#5B6A8B','Африка':'#9B5510',
    'Северная Америка':'#3D6B8A','Южная Америка':'#7A4D2A','Океания':'#3D8A8A',
    // Federal districts (distinct within Russia)
    'ЦФО':'#A1361B','ЮФО':'#C26B3C','ПФО':'#9B5510','СКФО':'#7A4D2A',
    'СФО':'#5B6A8B','ДФО':'#1B7A3E','СЗФО':'#3D8A8A','УФО':'#3D6B8A',
    // Specific regions & countries — warm amber (like handoff #B45309)
    'Краснодарский Край':'#B45309','Воронежская Область':'#B45309',
    'Московская Область':'#B45309','Владимирская Область':'#B45309',
    'Ставропольский Край':'#B45309','Москва':'#B45309',
    'Новосибирская Область':'#B45309','Волгоградская Область':'#B45309',
    'Саратовская Область':'#B45309','Алтайский Край':'#B45309',
    'Курская Область':'#B45309','Амурская Область':'#B45309',
    'Белгородская Область':'#B45309','Брянская Область':'#B45309',
    'Ростовская Область':'#B45309','Самарская Область':'#B45309',
    'Тамбовская Область':'#B45309','Красноярский Край':'#B45309',
    'Приморский Край':'#B45309','Омская Область':'#B45309',
    'Орловская Область':'#B45309','Тульская Область':'#B45309',
    'Пермский Край':'#B45309','Свердловская Область':'#B45309',
    'Иркутская Область':'#B45309','Оренбургская Область':'#B45309',
    'Республика Татарстан':'#B45309','Республика Башкортостан':'#B45309',
    'Тюменская Область':'#B45309','Рязанская Область':'#B45309',
    'Пензенская Область':'#B45309','Ульяновская Область':'#B45309',
    'Казахстан':'#B45309','Китай':'#B45309','Индия':'#B45309','Турция':'#B45309',
    'Египет':'#B45309','Белоруссия':'#B45309','Узбекистан':'#B45309',
    'Бразилия':'#B45309','Аргентина':'#B45309','США':'#B45309','Канада':'#B45309',
    'Германия':'#B45309','Франция':'#B45309','Иран':'#B45309',
    'Саудовская Аравия':'#B45309','ОАЭ':'#B45309','Азербайджан':'#B45309',
  },
};

const INI_OVR: Record<string, string> = {
  'Россия':'РФ','Северная Америка':'СА','Южная Америка':'ЮА',
  'Жмых/Шрот':'ЖШ','Мука/Крупа':'МК',
};

const IMPACT_LABELS: Record<string, string> = {
  positive: 'ПОЗИТИВ', negative: 'РИСК', neutral: 'НЕЙТРАЛЬНО', watch: 'СЛЕДИТЬ',
};

// Deterministic fallback palette for nodes not listed in PAL.
// Muted tones that fit the dark UI; consistent per name via djb2 hash.
const FALLBACK_COLORS = [
  '#4A6E8A','#6B8E5A','#8A6B4A','#5A4E8A','#8A4A6B',
  '#5A8A7A','#8A7A4A','#6B4A8A','#4A8A5A','#7A8A4A',
  '#4A7A8A','#8A4A4A','#6B7A4A','#8A5A7A','#4A8A8A',
  '#7A6B8A','#8A6B6B','#4A6B5A',
];
function hashColor(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  return FALLBACK_COLORS[((h >>> 0) % FALLBACK_COLORS.length)];
}
function nodeColor(ax: Ax, k: string): string {
  return PAL[ax]?.[k] ?? hashColor(k);
}

function getInitials(k: string): string {
  if (INI_OVR[k]) return INI_OVR[k];
  const w = k.replace(/\//g, ' ').split(/[\s-]+/).filter(Boolean);
  if (w.length >= 2) return (w[0][0] + w[1][0]).toUpperCase();
  return k.slice(0, 2);
}

// ─── Bucket helpers ───────────────────────────────────────────────────────────

/** Format a Date as local YYYY-MM-DD (avoids UTC-offset shift from toISOString) */
function toLocalDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Get bucket key for a date string and scale */
function getBucketKey(dateStr: string, scale: Scale): string {
  const d = new Date(`${dateStr}T00:00:00`);
  const y = d.getFullYear();
  const m = d.getMonth(); // 0-based
  switch (scale) {
    case 'week': {
      // number of weeks since WEEK_EPOCH
      const wn = Math.floor((d.getTime() - WEEK_EPOCH) / (7 * 86400000));
      return `W${wn}`;
    }
    case 'month':
      return `${y}-${String(m + 1).padStart(2, '0')}`;
    case 'quarter':
      return `${y}-Q${Math.ceil((m + 1) / 3)}`;
    case 'year':
      return `${y}`;
  }
}

/** Human-readable short label for a bucket */
function getBucketLabel(key: string, scale: Scale): string {
  switch (scale) {
    case 'week': {
      const wn = parseInt(key.slice(1));
      const d = new Date(WEEK_EPOCH + wn * 7 * 86400000);
      return `${d.getDate()} ${MON[d.getMonth()]}`;
    }
    case 'month': {
      const parts = key.split('-');
      const mo = parseInt(parts[1]) - 1;
      return `${MON_CAP[mo]} ${parts[0].slice(2)}`;
    }
    case 'quarter': {
      const [y, q] = key.split('-Q');
      return `${q}кв ${y.slice(2)}`;
    }
    case 'year':
      return key;
  }
}

/** Local date range [from, to] for a bucket.
 *  Uses toLocalDate() everywhere — toISOString() returns UTC dates which are
 *  off by the timezone offset (e.g. UTC+3 shifts dates back 1 day), causing
 *  onSelectRange() to pass wrong boundaries to the API. */
function getBucketDateRange(key: string, scale: Scale): [string, string] {
  switch (scale) {
    case 'week': {
      const wn = parseInt(key.slice(1));
      // WEEK_EPOCH is a local-time midnight, so adding N*7days gives local Monday midnight
      const start = new Date(WEEK_EPOCH + wn * 7 * 86400000);
      const end   = new Date(WEEK_EPOCH + (wn + 1) * 7 * 86400000 - 86400000);
      return [toLocalDate(start), toLocalDate(end)];
    }
    case 'month': {
      const [y, m] = key.split('-').map(Number);
      return [
        toLocalDate(new Date(y, m - 1, 1)),
        toLocalDate(new Date(y, m, 0)),
      ];
    }
    case 'quarter': {
      const y = parseInt(key.split('-Q')[0]);
      const q = parseInt(key.split('-Q')[1]);
      const sm = (q - 1) * 3;
      return [
        toLocalDate(new Date(y, sm, 1)),
        toLocalDate(new Date(y, sm + 3, 0)),
      ];
    }
    case 'year': {
      const y = parseInt(key);
      return [`${y}-01-01`, `${y}-12-31`];
    }
  }
}

// ─── Reducer ──────────────────────────────────────────────────────────────────

function cloneFilters(f: Record<Ax, Set<string>>): Record<Ax, Set<string>> {
  return { c: new Set(f.c), p: new Set(f.p), g: new Set(f.g) };
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'SET_SCALE':
      return { ...state, scale: action.scale, selectedBuckets: new Set(), expanded: { c: false, p: false, g: false } };
    case 'SET_METRIC':
      return { ...state, metric: action.metric };
    case 'TOGGLE_HIDE_COMMON':
      return { ...state, hideCommon: !state.hideCommon };
    case 'TOGGLE_FLOW':
      return { ...state, flow: !state.flow };
    case 'SET_CENTER':
      return { ...state, center: action.ax };
    case 'TOGGLE_FILTER': {
      const f = cloneFilters(state.filters);
      if (f[action.ax].has(action.k)) f[action.ax].delete(action.k);
      else f[action.ax].add(action.k);
      return { ...state, filters: f };
    }
    case 'REMOVE_FILTER': {
      const f = cloneFilters(state.filters);
      f[action.ax].delete(action.k);
      return { ...state, filters: f };
    }
    case 'CLEAR_FILTERS':
      return { ...state, filters: { c: new Set(), p: new Set(), g: new Set() } };
    case 'SET_FILTERS':
      return { ...state, filters: action.filters };
    case 'TOGGLE_EXPANDED':
      return { ...state, expanded: { ...state.expanded, [action.ax]: !state.expanded[action.ax] } };
    case 'TOGGLE_BUCKET': {
      // Single-select: click active bucket → deselect; click another → replace
      if (state.selectedBuckets.has(action.key)) {
        return { ...state, selectedBuckets: new Set() };
      }
      return { ...state, selectedBuckets: new Set([action.key]) };
    }
    case 'CLEAR_BUCKETS':
      return { ...state, selectedBuckets: new Set() };
    case 'OPEN_SETTINGS':
      return { ...state, settingsOpen: true };
    case 'CLOSE_SETTINGS':
      return { ...state, settingsOpen: false };
  }
}

const initialState: State = {
  scale: 'month', metric: 'freq', hideCommon: false, flow: true,
  center: 'p',
  filters: { c: new Set(), p: new Set(), g: new Set() },
  expanded: { c: false, p: false, g: false },
  selectedBuckets: new Set(), settingsOpen: false,
};

// ─── Data helpers ─────────────────────────────────────────────────────────────

type EventLike = { date_from: string | null; topics: string[]; regions: string[]; products: string[]; title?: string };

function buildEvRows(events: EventLike[]): { rows: EvRow[]; newestDate: string } {
  if (!events.length) return { rows: [], newestDate: '' };

  // Seed exclusion sets from the PAL taxonomy first.
  // Any name already registered as a product or region in PAL must NEVER appear
  // in the ТЕМА column — even if the backend wrote it into event.topics.
  // Then also add names from actual event data to cover backend-only values.
  const globalProducts = new Set<string>(Object.keys(PAL.p));
  const globalRegions  = new Set<string>(Object.keys(PAL.g));
  events.forEach(e => {
    (e.products || []).forEach(v => globalProducts.add(v));
    (e.regions  || []).forEach(v => globalRegions.add(v));
  });

  const sorted = [...events].sort((a, b) => (b.date_from || '').localeCompare(a.date_from || ''));
  const newestDate = sorted[0].date_from || new Date().toISOString().slice(0, 10);
  const rows: EvRow[] = sorted.map(e => {
    const p = e.products || [];
    const g = e.regions  || [];
    // Strip from topics anything that appears as product or region in ANY event
    const c = (e.topics || []).filter(t => !globalProducts.has(t) && !globalRegions.has(t));
    return { c, p, g, dateFrom: e.date_from || newestDate, title: e.title ?? '' };
  });
  return { rows, newestDate };
}

function rangeLabel(df: string, dt: string): string {
  const from = new Date(`${df}T00:00:00`);
  const to   = new Date(`${dt}T00:00:00`);
  if (df === dt) return `${from.getDate()} ${MON[from.getMonth()]}`;
  if (from.getMonth() === to.getMonth()) return `${from.getDate()}–${to.getDate()} ${MON[to.getMonth()]}`;
  return `${from.getDate()} ${MON[from.getMonth()]} – ${to.getDate()} ${MON[to.getMonth()]}`;
}

function formatEvDate(dateStr: string | null): string {
  if (!dateStr) return '—';
  const d = new Date(`${dateStr}T00:00:00`);
  return `${d.getDate()} ${MON_CAP[d.getMonth()]}`;
}

function countAxis(evs: EvRow[], ax: Ax): Record<string, number> {
  const c: Record<string, number> = {};
  evs.forEach(e => e[ax].forEach(k => { c[k] = (c[k] || 0) + 1; }));
  return c;
}

function axTopFiltered(evs: EvRow[], ax: Ax, n: number, hideCommon: boolean): Array<{ k: string; n: number }> {
  const c = countAxis(evs, ax);
  const N = evs.length;
  let keys = Object.keys(c);
  if (hideCommon) keys = keys.filter(k => c[k] / N <= 0.5);
  return keys.sort((a, b) => c[b] - c[a]).slice(0, n).map(k => ({ k, n: c[k] }));
}

function axTotalFiltered(evs: EvRow[], ax: Ax, hideCommon: boolean): number {
  const c = countAxis(evs, ax);
  const N = evs.length;
  let keys = Object.keys(c);
  if (hideCommon) keys = keys.filter(k => c[k] / N <= 0.5);
  return keys.length;
}

function anyFilter(filters: Record<Ax, Set<string>>): boolean {
  return filters.c.size > 0 || filters.p.size > 0 || filters.g.size > 0;
}

function passFilter(e: EvRow, skip: Ax | null, filters: Record<Ax, Set<string>>): boolean {
  for (const a of ['c', 'p', 'g'] as Ax[]) {
    if (a === skip) continue;
    const f = filters[a];
    if (f.size && !e[a].some(x => f.has(x))) return false;
  }
  return true;
}

// ─── SVG helper ───────────────────────────────────────────────────────────────

function svgEl<T extends SVGElement>(tag: string, attrs: Record<string, string | number> = {}): T {
  const el = document.createElementNS(SVG_NS, tag) as T;
  Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, String(v)));
  return el;
}

// ─── Graph renderer hook ──────────────────────────────────────────────────────

function useRenderGraph(
  svgRef: React.RefObject<SVGSVGElement>,
  evs: EvRow[],
  state: State,
  dispatch: React.Dispatch<Action>,
  tip: { show: (x: number, y: number, html: string) => void; hide: () => void },
  onToggle: (ax: Ax, k: string) => void,
): () => GraphInfo | null {
  // Ref keeps onToggle always fresh without being a useCallback dep.
  // This prevents the graph from re-rendering every time App.tsx re-renders
  // (which recreates inline arrow functions on every render).
  const onToggleRef = useRef(onToggle);
  onToggleRef.current = onToggle;

  return useCallback((): GraphInfo | null => {
    const svg = svgRef.current;
    if (!svg) return null;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    const compact = window.innerWidth <= 700;
    const topn = topnFor(compact);
    const N = evs.length || 1;
    const nc = state.expanded.c ? EXPN : topn;
    const np = state.expanded.p ? EXPN : topn;
    const ng = state.expanded.g ? EXPN : topn;

    const cCnt = countAxis(evs, 'c');
    const pCnt = countAxis(evs, 'p');
    const gCnt = countAxis(evs, 'g');

    const axList = (ax: Ax, cnt: Record<string, number>, n: number) =>
      state.filters[ax].size
        ? [...state.filters[ax]].map(k => ({ k, n: cnt[k] || 0 })).sort((a, b) => b.n - a.n)
        : axTopFiltered(evs, ax, n, state.hideCommon);

    const cc = axList('c', cCnt, nc);
    const pc = axList('p', pCnt, np);
    const gc = axList('g', gCnt, ng);
    const ccK = new Set(cc.map(o => o.k));
    const pcK = new Set(pc.map(o => o.k));
    const gcK = new Set(gc.map(o => o.k));

    // All 3 co-occurrence pairs (needed for column rotation)
    const cpFreq: Record<string, number> = {};
    const pgFreq: Record<string, number> = {};
    const cgFreq: Record<string, number> = {};
    evs.forEach(e => {
      e.c.filter(x => ccK.has(x)).forEach(a => {
        e.p.filter(x => pcK.has(x)).forEach(b => { const key = `${a}|${b}`; cpFreq[key] = (cpFreq[key] || 0) + 1; });
        e.g.filter(x => gcK.has(x)).forEach(b => { const key = `${a}|${b}`; cgFreq[key] = (cgFreq[key] || 0) + 1; });
      });
      e.p.filter(x => pcK.has(x)).forEach(a =>
        e.g.filter(x => gcK.has(x)).forEach(b => { const key = `${a}|${b}`; pgFreq[key] = (pgFreq[key] || 0) + 1; })
      );
    });

    const metricVal = (freq: number, cntA: number, cntB: number): number => {
      if (state.metric === 'freq') return freq;
      if (freq < 3) return 0;
      return (freq * N) / (cntA * cntB);
    };

    const W = compact ? 360 : 1160;
    const rowH = compact ? 40 : 42;
    const padTop = compact ? 8 : 14;   // headers now live in HTML row above the graph
    const NW = compact ? 34 : 180;

    // Column order depends on which axis is "center"
    const ctr = state.center;
    const ord: [Ax, Ax, Ax] = ctr === 'c' ? ['p', 'c', 'g'] : ctr === 'g' ? ['c', 'g', 'p'] : ['c', 'p', 'g'];
    const axData: Record<Ax, Array<{ k: string; n: number }>> = { c: cc, p: pc, g: gc };
    const cols = ord.map(ax => [ax, axData[ax]] as [Ax, Array<{ k: string; n: number }>]);

    const colX = [20, W / 2 - NW / 2, W - 20 - NW];
    const maxRows = Math.max(cc.length, pc.length, gc.length, 1);
    const H = padTop + maxRows * rowH + 10;

    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

    // Node positions (column headers are rendered as HTML above the graph)
    const pos: Record<string, NodePos> = {};
    cols.forEach(([ax, arr], ci) => {
      const offset = padTop + (maxRows - arr.length) * rowH / 2;
      arr.forEach((o, i) => {
        pos[`${ax}|${o.k}`] = { x: colX[ci], y: offset + i * rowH + rowH / 2, ax, k: o.k, n: o.n, ci };
      });
    });

    const gLinks = svgEl<SVGGElement>('g');
    const gFlows = svgEl<SVGGElement>('g');
    const gNodes = svgEl<SVGGElement>('g');
    svg.appendChild(gLinks);
    svg.appendChild(gFlows);
    svg.appendChild(gNodes);

    const allLinks: LinkItem[] = [];
    // buildLinks handles arbitrary left/right positioning
    const buildLinks = (
      obj: Record<string, number>, axA: Ax, axB: Ax,
      cntA: Record<string, number>, cntB: Record<string, number>
    ) => {
      Object.keys(obj).forEach(key => {
        const [a, b] = key.split('|');
        const pa = pos[`${axA}|${a}`], pb = pos[`${axB}|${b}`];
        if (!pa || !pb) return;
        const freq = obj[key];
        const mv = metricVal(freq, cntA[a] || 1, cntB[b] || 1);
        if (mv <= 0) return;
        const aIsLeft = pa.x <= pb.x;
        const x1 = aIsLeft ? pa.x + NW : pa.x;
        const x2 = aIsLeft ? pb.x : pb.x + NW;
        const y1 = pa.y, y2 = pb.y, mx = (x1 + x2) / 2;
        const d = `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
        allLinks.push({ axA, a, axB, b, freq, mv, pa, pb, d, wdt: 0 });
      });
    };

    // Freq lookup by canonical pair key; route links for the two adjacent column pairs
    const freqMap: Record<string, [Record<string, number>, Record<string, number>, Record<string, number>]> = {
      'c|p': [cpFreq, cCnt, pCnt], 'p|g': [pgFreq, pCnt, gCnt], 'c|g': [cgFreq, cCnt, gCnt],
    };
    const buildPair = (ax1: Ax, ax2: Ax) => {
      const fwd = freqMap[`${ax1}|${ax2}`];
      if (fwd) { buildLinks(fwd[0], ax1, ax2, fwd[1], fwd[2]); return; }
      const rev = freqMap[`${ax2}|${ax1}`];
      if (rev) buildLinks(rev[0], ax2, ax1, rev[1], rev[2]);
    };
    buildPair(ord[0], ord[1]);
    buildPair(ord[1], ord[2]);
    const maxMV = Math.max(...allLinks.map(L => L.mv), 0.001);
    allLinks.forEach(L => { L.wdt = L.mv / maxMV * 9 + 0.7; });

    const linkPaths: SVGPathElement[] = [];
    allLinks.forEach((L, i) => {
      const path = svgEl<SVGPathElement>('path', {
        d: L.d, class: 'glink',
        stroke: nodeColor(L.axA, L.a),
        'stroke-width': L.wdt,
        'stroke-opacity': 0.22,
        pathLength: 1,
      });
      path.setAttribute('data-nodes', `${L.axA}|${L.a},${L.axB}|${L.b}`);
      path.style.strokeDasharray = '1';
      path.style.strokeDashoffset = '1';
      path.style.transition = `stroke-dashoffset 600ms ease ${i * 8}ms`;
      gLinks.appendChild(path);
      linkPaths.push(path);

      const makeLinkTip = () => {
        const liftTxt = state.metric === 'lift'
          ? `<div class="gtip-l"><span class="gtip-lbl">неожиданность</span><span class="gtip-v">×${L.mv.toFixed(1)}</span></div>` : '';
        return `<div class="gtip-d">${L.a} → ${L.b}</div><div class="gtip-l"><span class="gtip-lbl">вместе</span><span class="gtip-v">${L.freq} соб.</span></div>${liftTxt}`;
      };
      path.addEventListener('mouseenter', (ev) => tip.show((ev as MouseEvent).clientX, (ev as MouseEvent).clientY, makeLinkTip()));
      path.addEventListener('mousemove', (ev) => tip.show((ev as MouseEvent).clientX, (ev as MouseEvent).clientY, makeLinkTip()));
      path.addEventListener('mouseleave', () => tip.hide());
      path.addEventListener('click', () => {
        tip.hide();
        onToggleRef.current(L.axA, L.a);
        onToggleRef.current(L.axB, L.b);
      });
    });
    requestAnimationFrame(() => linkPaths.forEach(p => { p.style.strokeDashoffset = '0'; }));

    Object.values(pos).forEach((p, i) => {
      const g = svgEl<SVGGElement>('g', { class: 'gnode' });
      g.setAttribute('data-node', `${p.ax}|${p.k}`);
      const base = nodeColor(p.ax, p.k);

      // Node style: compact = arc-circle on dark bg; desktop = white circle badge + dark text
      const fr = Math.max(0.06, Math.min(1, p.n / N));
      if (compact) {
        const cx = p.x + NW / 2;
        const NR = 12, RT = 9.5, SW = 2.4, C = 2 * Math.PI * RT;
        const dashArr = `${(C * fr).toFixed(1)} ${(C * (1 - fr)).toFixed(1)}`;
        g.appendChild(svgEl('circle', { cx, cy: p.y, r: NR + 1, fill: '#fff', stroke: '#E1E0DA', 'stroke-width': 1, class: 'gn-base' }));
        g.appendChild(svgEl('circle', { cx, cy: p.y, r: RT, fill: 'none', stroke: '#E8E7E2', 'stroke-width': SW }));
        g.appendChild(svgEl('circle', { cx, cy: p.y, r: RT, fill: 'none', stroke: base, 'stroke-width': SW, 'stroke-linecap': 'round', 'stroke-dasharray': dashArr, transform: `rotate(-90 ${cx} ${p.y})` }));
        const ini = svgEl<SVGTextElement>('text', { x: cx, y: p.y + 3.5, 'text-anchor': 'middle', class: 'gn-ini' });
        ini.setAttribute('fill', base); ini.setAttribute('font-size', '9');
        ini.textContent = getInitials(p.k);
        g.appendChild(ini);
        const shortName = p.k.length > 13 ? `${p.k.slice(0, 12).trim()}…` : p.k;
        const nn = svgEl<SVGTextElement>('text', { x: cx, y: p.y + NR + 12, 'text-anchor': 'middle', class: 'gn-lbl2' });
        nn.textContent = shortName;
        g.appendChild(nn);
      } else {
        // White pill + ring track + colored progress arc (handoff design)
        const RT = 11.5, SW = 3, ccx = p.x + 18, C = 2 * Math.PI * RT;
        const dashArr = `${(C * fr).toFixed(1)} ${(C * (1 - fr)).toFixed(1)}`;
        // White pill background
        g.appendChild(svgEl('rect', { x: p.x, y: p.y - 17, width: NW, height: 34, rx: 17, fill: '#fff', stroke: '#E8E7E2', 'stroke-width': 1, class: 'gn-base' }));
        // Ring track (light)
        g.appendChild(svgEl('circle', { cx: ccx, cy: p.y, r: RT, fill: 'none', stroke: '#E8E7E2', 'stroke-width': SW }));
        // Ring arc (colored, progress)
        g.appendChild(svgEl('circle', { cx: ccx, cy: p.y, r: RT, fill: 'none', stroke: base, 'stroke-width': SW, 'stroke-linecap': 'round', 'stroke-dasharray': dashArr, transform: `rotate(-90 ${ccx} ${p.y})` }));
        // Initials (colored)
        const ini = svgEl<SVGTextElement>('text', { x: ccx, y: p.y + 4, 'text-anchor': 'middle', class: 'gn-ini' });
        ini.setAttribute('fill', base); ini.setAttribute('font-size', '10');
        ini.textContent = getInitials(p.k);
        g.appendChild(ini);
        // Label (dark, truncated to fit between ring and count)
        const countW = String(p.n).length * 7 + 4;
        const budget = NW - 36 - countW - 12;
        const maxC = Math.max(4, Math.floor(budget / 6.6));
        const lbl = svgEl<SVGTextElement>('text', { x: p.x + 36, y: p.y + 4, class: 'gn-lbl' });
        lbl.textContent = p.k.length > maxC ? `${p.k.slice(0, maxC - 1).trim()}…` : p.k;
        g.appendChild(lbl);
        // Count (right-aligned, muted)
        const nn = svgEl<SVGTextElement>('text', { x: p.x + NW - 12, y: p.y + 4, 'text-anchor': 'end', class: 'gn-n2' });
        nn.textContent = String(p.n);
        g.appendChild(nn);
      }

      if (state.filters[p.ax].size && !state.filters[p.ax].has(p.k)) g.classList.add('dim');

      if (state.filters[p.ax].has(p.k)) {
        let bx: number, by: number;
        if (compact) {
          g.appendChild(svgEl('circle', { cx: p.x + NW / 2, cy: p.y, r: 16, fill: 'none', stroke: '#15140f', 'stroke-width': 2.5 }));
          bx = p.x + NW / 2 + 13; by = p.y - 13;
        } else {
          // Selection outline around the pill
          g.appendChild(svgEl('rect', { x: p.x - 2, y: p.y - 19, width: NW + 4, height: 38, rx: 19, fill: 'none', stroke: '#15140f', 'stroke-width': 2.5 }));
          bx = p.x + NW - 3; by = p.y - 15;
        }
        g.appendChild(svgEl('circle', { cx: bx, cy: by, r: 6.5, fill: '#15140f', stroke: '#fff', 'stroke-width': 1.5 }));
        const ck = svgEl<SVGTextElement>('text', { x: bx, y: by + 3, 'text-anchor': 'middle', class: 'gn-ck' });
        ck.textContent = '✓';
        g.appendChild(ck);
      }

      const delay = p.ci * 60 + i * 6;
      g.style.opacity = '0';
      g.style.transform = `translateX(${p.ci === 0 ? -10 : p.ci === 2 ? 10 : 0}px)`;
      g.style.transition = `opacity 360ms ease ${delay}ms, transform 360ms ease ${delay}ms`;
      requestAnimationFrame(() => { g.style.opacity = '1'; g.style.transform = 'translateX(0)'; });

      const nodeKey = `${p.ax}|${p.k}`;

      const makeNodeTip = () => {
        const rel = allLinks
          .filter(L => `${L.axA}|${L.a}` === nodeKey || `${L.axB}|${L.b}` === nodeKey)
          .sort((a, b) => b.mv - a.mv);
        let h = `<div class="gtip-d">${p.k} · ${AXNAME[p.ax]}</div><div class="gtip-l"><span class="gtip-lbl">событий</span><span class="gtip-v">${p.n}</span></div>`;
        rel.slice(0, 4).forEach(L => {
          const isA = `${L.axA}|${L.a}` === nodeKey;
          const oAx = isA ? L.axB : L.axA;
          const oK  = isA ? L.b : L.a;
          const val = state.metric === 'lift' ? `×${L.mv.toFixed(1)}` : String(L.freq);
          h += `<div class="gtip-l"><span class="gtip-p" style="background:${nodeColor(oAx, oK)}"></span><span class="gtip-lbl">${oK}</span><span class="gtip-v">${val}</span></div>`;
        });
        return h;
      };

      g.addEventListener('mouseenter', () => {
        svg.querySelectorAll<SVGPathElement>('.glink').forEach(l => {
          const nodes = l.getAttribute('data-nodes')?.split(',') ?? [];
          if (!nodes.includes(nodeKey)) l.classList.add('dim');
          else { l.classList.remove('dim'); l.setAttribute('stroke-opacity', '0.7'); }
        });
        const conn = new Set([nodeKey]);
        svg.querySelectorAll<SVGPathElement>('.glink').forEach(l => {
          const nodes = l.getAttribute('data-nodes')?.split(',') ?? [];
          if (nodes.includes(nodeKey)) nodes.forEach(n => conn.add(n));
        });
        svg.querySelectorAll<SVGGElement>('.gnode').forEach(gn => {
          const nk = gn.getAttribute('data-node');
          if (!nk) return;
          if (conn.has(nk)) gn.classList.add('hi');
          else gn.classList.add('dim');
        });
      });

      g.addEventListener('mouseleave', () => {
        svg.querySelectorAll<SVGPathElement>('.glink').forEach(l => {
          l.classList.remove('dim');
          l.setAttribute('stroke-opacity', '0.22');
        });
        svg.querySelectorAll<SVGGElement>('.gnode').forEach(gn => {
          gn.classList.remove('hi', 'dim');
          const nk = gn.getAttribute('data-node');
          if (!nk) return;
          const [ax2, k2] = nk.split('|') as [Ax, string];
          if (state.filters[ax2].size && !state.filters[ax2].has(k2)) gn.classList.add('dim');
        });
        tip.hide();
      });

      g.addEventListener('mousemove', (ev) => tip.show((ev as MouseEvent).clientX, (ev as MouseEvent).clientY, makeNodeTip()));
      g.addEventListener('click', () => { tip.hide(); onToggleRef.current(p.ax, p.k); });
      gNodes.appendChild(g);
    });

    if (state.flow) {
      const sorted = [...allLinks].sort((a, b) => b.mv - a.mv).slice(0, Math.min(allLinks.length, 26));
      sorted.forEach(L => {
        const pe = svgEl<SVGPathElement>('path', { d: L.d, fill: 'none', stroke: 'none' });
        gFlows.appendChild(pe);
        const len = pe.getTotalLength();
        const cnt = L.freq > 10 ? 2 : 1;
        for (let qi = 0; qi < cnt; qi++) {
          const dot = svgEl<SVGCircleElement>('circle', {
            r: Math.min(2.6, L.wdt * 0.5 + 1),
            class: 'gflow',
            fill: nodeColor(L.axA, L.a),
          });
          dot.style.opacity = '0.6';
          gFlows.appendChild(dot);
          const dur = 1800 + Math.random() * 1600 + L.freq * 20;
          const startT = performance.now() + Math.random() * dur;
          const frame = (now: number) => {
            if (!dot.isConnected) return;
            let t = ((now - startT) % dur) / dur;
            if (t < 0) { requestAnimationFrame(frame); return; }
            const pt = pe.getPointAtLength(t * len);
            dot.setAttribute('cx', String(pt.x));
            dot.setAttribute('cy', String(pt.y));
            dot.style.opacity = String((0.22 + Math.sin(t * Math.PI) * 0.5).toFixed(2));
            requestAnimationFrame(frame);
          };
          requestAnimationFrame(frame);
        }
      });
    }

    const top = [...allLinks].sort((a, b) => b.mv - a.mv)[0];
    return {
      topLink: top ? `${top.a} → ${top.b}${state.metric === 'lift' ? ` (×${top.mv.toFixed(1)})` : ` (${top.freq})`}` : '—',
      topLabel: state.metric === 'lift' ? 'самая неожиданная: ' : 'топ-связка: ',
      evCount: evs.length,
      totals: {
        c: axTotalFiltered(evs, 'c', state.hideCommon),
        p: axTotalFiltered(evs, 'p', state.hideCommon),
        g: axTotalFiltered(evs, 'g', state.hideCommon),
      },
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [evs, state, dispatch, tip]); // onToggle intentionally omitted — accessed via onToggleRef
}

// ─── Mini Timeline ────────────────────────────────────────────────────────────

type BucketData = {
  key: string;
  label: string;
  total: number;
  matched: number;
};

function MiniTimeline({
  rows, state, dispatch,
}: {
  rows: EvRow[];
  state: State;
  dispatch: React.Dispatch<Action>;
}) {
  // Build bucket list for current scale, newest first (left)
  const { buckets, maxCount } = useMemo(() => {
    const map: Record<string, { total: number; matched: number }> = {};
    rows.forEach(e => {
      const key = getBucketKey(e.dateFrom, state.scale);
      if (!map[key]) map[key] = { total: 0, matched: 0 };
      map[key].total++;
      if (!anyFilter(state.filters) || passFilter(e, null, state.filters)) {
        map[key].matched++;
      }
    });
    // Reverse sort: newest key first → displayed on the LEFT
    const sorted: BucketData[] = Object.keys(map)
      .sort()
      .reverse()
      .map(key => ({ key, label: getBucketLabel(key, state.scale), ...map[key] }));
    const maxCount = Math.max(...sorted.map(b => b.total), 1);
    return { buckets: sorted, maxCount };
  }, [rows, state.scale, state.filters]);

  const hasSel = state.selectedBuckets.size > 0;

  // Selected bucket label (single-select → always one bucket)
  const selLabel = useMemo(() => {
    if (!hasSel) return null;
    const [key] = [...state.selectedBuckets];
    return getBucketLabel(key, state.scale);
  }, [state.selectedBuckets, state.scale, hasSel]);

  // Fixed px width per bar by scale — bars don't grow; CSS handles min-width:100%/max-content
  const colW = state.scale === 'week' ? 30 : state.scale === 'month' ? 46 : state.scale === 'quarter' ? 66 : 90;

  // Adaptive label display: show every Nth to avoid crowding
  const showEvery = buckets.length <= 18 ? 1 : buckets.length <= 36 ? 2 : 4;

  // Bar area height (total column height minus label row)
  const BAR_H = 76;   // px available for bars
  const LBL_H = 20;   // px for label row
  const COL_H = BAR_H + LBL_H;

  // Mid guide value (50% of max) — we skip the top guide (at container border, label overflows)
  const midGuide = maxCount > 1 ? Math.round(maxCount / 2) : null;

  return (
    <div className="eg-tl-wrap">
      <div className="eg-tl-hd">
        {hasSel ? (
          <span className="eg-tl-t">
            Период: <span className="eg-tl-rng">{selLabel}</span>
            <span className="eg-tl-clr" onClick={() => dispatch({ type: 'CLEAR_BUCKETS' })}>сбросить ✕</span>
          </span>
        ) : (
          <span className="eg-tl-t">История событий · кликните период для фильтра</span>
        )}
      </div>
      <div className="eg-tl-scroll">
        <div className="eg-tl" style={{ height: `${COL_H}px` }}>
          {/* Only mid guide — top guide sits at container border and its label overflows */}
          {midGuide !== null && (
            <div className="eg-tl-gl" style={{ bottom: `${LBL_H + 0.5 * BAR_H}px` }}>
              <span>{midGuide}</span>
            </div>
          )}

          {buckets.map((b, idx) => {
            const isSel = state.selectedBuckets.has(b.key);
            // Single-select: dim all unselected buckets when any bucket is active
            const dim = hasSel && !isSel;
            const showLabel = idx % showEvery === 0 || idx === buckets.length - 1;
            return (
              <div
                key={b.key}
                className={`eg-tl-col${isSel ? ' sel' : (dim ? ' out' : '')}`}
                style={{ flex: `0 0 ${colW}px` }}
                onClick={() => dispatch({ type: 'TOGGLE_BUCKET', key: b.key })}
                title={`${b.label}: ${b.total} событий`}
              >
                {/* Bar area */}
                <div className="eg-tl-col-bars">
                  <div className="eg-tl-bar" style={{ height: `${(b.total / maxCount) * 100}%` }}>
                    {b.matched > 0 && (
                      <div className="eg-tl-seg" style={{ height: `${(b.matched / Math.max(b.total, 1)) * 100}%`, background: '#52617F' }} />
                    )}
                    {b.matched < b.total && (
                      <div className="eg-tl-seg" style={{ height: `${((b.total - b.matched) / Math.max(b.total, 1)) * 100}%`, background: '#D9DAE0' }} />
                    )}
                  </div>
                </div>
                {/* Inline label */}
                <div className="eg-tl-lbl">{showLabel ? b.label : ''}</div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ─── Event card ───────────────────────────────────────────────────────────────

function EventCard({ ev, selectedRole, onOpenNews, onTagClick }: {
  ev: EventItem;
  selectedRole?: Filters['role'];
  onOpenNews: (id: number) => void;
  onTagClick: (tag: string) => void;
}) {
  const dateFrom = formatEvDate(ev.date_from);
  const dateTo   = ev.date_to && ev.date_to !== ev.date_from ? formatEvDate(ev.date_to) : null;
  const dateRange = dateTo ? `${dateFrom} — ${dateTo}` : dateFrom;
  const sigmaClass = ev.sigma >= 85 ? 'hi' : ev.sigma >= 70 ? 'md' : 'lo';
  // Какой impact-чип раскрыт (для мобильного аккордеона; на десктопе работает hover-тултип)
  const [openImpact, setOpenImpact] = useState<string | null>(null);
  const [showAllImpacts, setShowAllImpacts] = useState(false);
  const openNews = () => { if (ev.main_news_id) onOpenNews(ev.main_news_id); };

  // Рекомендации: нейтральные роли скрыты. Порядок — сначала риск/позитив, потом «следить».
  // Если юзер выбрал роль в фильтрах — она показывается первой (и одна по умолчанию);
  // иначе показываем первые 3. Остальное — под кнопкой «ещё».
  const impactRank = (imp: EventItem['impacts'][number]) => (imp.impact === 'watch' ? 1 : 0);
  const visibleImpacts = (ev.impacts || [])
    .filter((imp) => imp.impact !== 'neutral')
    .slice()
    .sort((a, b) => impactRank(a) - impactRank(b)); // stable: риск/позитив сохраняют исходный порядок
  const selectedImpact = selectedRole ? visibleImpacts.find((imp) => imp.role === selectedRole) : undefined;
  const orderedImpacts = selectedImpact
    ? [selectedImpact, ...visibleImpacts.filter((imp) => imp !== selectedImpact)]
    : visibleImpacts;
  const baseCount = selectedImpact ? 1 : 3;
  const shownImpacts = showAllImpacts ? orderedImpacts : orderedImpacts.slice(0, baseCount);
  const hiddenImpactCount = orderedImpacts.length - shownImpacts.length;

  return (
    <div
      className={`ev-card${ev.main_news_id ? ' ev-card-click' : ''}`}
      onClick={openNews}
      role={ev.main_news_id ? 'button' : undefined}
      tabIndex={ev.main_news_id ? 0 : undefined}
    >
      {/* Header row */}
      <div className="ev-card-hd">
        <span className="ev-card-k">
          <span className="ev-dot" />
          <span className="ev-card-lbl">СОБЫТИЕ</span>
          <span className="ev-card-date">· {dateRange}</span>
        </span>
        <span className={`ev-sigma ev-sg-${sigmaClass}`}>Σ {ev.sigma}%</span>
      </div>

      {/* Body — single column */}
      <div className="ev-card-inner">
        <h3 className={`ev-card-title${ev.main_news_id ? ' ev-title-link' : ''}`}>
          {ev.title}
        </h3>

        {ev.summary && (
          <p className="ev-card-summary">{cleanSummary(ev.summary)}</p>
        )}

        {(ev.topics?.length > 0 || ev.regions?.length > 0 || ev.products?.length > 0) && (
          <div className="ev-card-tags">
            {/* Чипы по оси: регион=янтарь, тема=синий, продукт=зелёный.
                Тема = всё, что не география и не продукт. */}
            {[
              ...(ev.regions || []).map(t => ({ t, kind: 'region' })),
              ...(ev.topics || []).map(t => ({ t, kind: 'topic' })),
              ...(ev.products || []).map(t => ({ t, kind: 'product' })),
            ].slice(0, 8).map(({ t, kind }) => (
              <button key={`${kind}:${t}`} className={`ev-tag fc-${kind}`} onClick={(e) => { e.stopPropagation(); onTagClick(t); }}>{t}</button>
            ))}
          </div>
        )}

        {/* Impact по ролям — компактный список: цветная полоска статуса + роль + подпись. Тап раскрывает детали.
            Нейтральные роли скрыты. Выбранная в фильтрах роль — первой; остальное под кнопкой «ещё». */}
        {shownImpacts.length > 0 && (
          <div className="ev-imp-list" onClick={(e) => e.stopPropagation()}>
            {shownImpacts.map(imp => {
              const hasDetail = Boolean(imp.summary || imp.action_hint);
              const open = openImpact === imp.role;
              return (
                <div key={imp.role} className={`ev-imp-item ev-imp-item-${imp.impact}${hasDetail ? ' has-detail' : ''}${open ? ' open' : ''}`}>
                  <button
                    type="button"
                    className="ev-imp-line"
                    onClick={(e) => { e.stopPropagation(); if (hasDetail) setOpenImpact(open ? null : imp.role); }}
                  >
                    <span className="ev-imp-role">{imp.label}</span>
                    <span className={`ev-imp-stat ev-imp-stat-${imp.impact}`}>
                      {IMPACT_LABELS[imp.impact] ?? imp.impact}
                    </span>
                  </button>
                  {hasDetail && open && (
                    <div className="ev-imp-detail">
                      {imp.summary && <span className="ev-imp-tip-sm">{cleanSummary(imp.summary)}</span>}
                      {imp.action_hint && <span className="ev-imp-tip-hint">{cleanSummary(imp.action_hint)}</span>}
                    </div>
                  )}
                </div>
              );
            })}
            {(hiddenImpactCount > 0 || showAllImpacts) && orderedImpacts.length > baseCount && (
              <button
                type="button"
                className="ev-imp-more"
                onClick={(e) => { e.stopPropagation(); setShowAllImpacts((v) => !v); }}
              >
                {showAllImpacts ? 'свернуть' : `ещё ${hiddenImpactCount}`}
              </button>
            )}
          </div>
        )}

        {/* Footer: источники + статистика. Открытие новости — клик по всей карточке. */}
        <div className="ev-card-foot">
          <div className="ev-card-foot-l">
            {ev.sources_count > 0 && (
              <span className="ev-sources-lnk">Источники · {ev.sources_count}</span>
            )}
          </div>
          <div className="ev-card-stats">
            <span className="ev-stat"><span className="num">{ev.news_count}</span> публ.</span>
            <span className="ev-stat-dot" />
            <span className="ev-stat"><span className="num">{ev.views.toLocaleString('ru-RU')}</span> просм.</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Events block ─────────────────────────────────────────────────────────────

export function EventsBlock({ events, total, loading, role, order = 'asc', hasMore, onShowMore, onOpenAllEvents, onOpenNews, onTagClick }: {
  events: EventItem[];
  total: number;
  loading?: boolean;
  role?: Filters['role'];
  order?: 'asc' | 'desc';      // 'desc' = самые свежие сверху (для главной)
  hasMore?: boolean;
  onShowMore?: () => void;
  onOpenAllEvents?: () => void;
  onOpenNews: (id: number) => void;
  onTagClick: (tag: string) => void;
}) {
  // По умолчанию старые→новые; order='desc' — самые свежие первыми.
  const sorted = useMemo(
    () => {
      const asc = [...events].sort((a, b) => (a.date_from ?? '').localeCompare(b.date_from ?? ''));
      return order === 'desc' ? asc.reverse() : asc;
    },
    [events, order],
  );

  if (!events.length) return null;
  return (
    <div className="ev-block">
      <div className="ev-block-hd">
        <span className="ev-block-k">СОБЫТИЯ</span>
        <span className="ev-block-cnt num">{total}</span>
        <span className="ev-block-sub">событий</span>
        {onOpenAllEvents && (
          <button className="ev-block-all" onClick={onOpenAllEvents}>Все события</button>
        )}
      </div>
      {loading && (
        <div className="events-inline-loader">
          <span className="loader-ring" aria-hidden="true" />
          <div>
            <b>Подбираю события</b>
            <span>фильтрую за выбранный период</span>
          </div>
        </div>
      )}
      <div
        className="ev-block-list"
        style={loading ? { opacity: 0.46, pointerEvents: 'none', filter: 'saturate(.82)', transition: 'opacity 180ms ease, filter 180ms ease' } : undefined}
      >
        {sorted.map(ev => (
          <EventCard key={ev.id} ev={ev} selectedRole={role ?? null} onOpenNews={onOpenNews} onTagClick={onTagClick} />
        ))}
      </div>
      {hasMore && onShowMore && (
        <div className="ev-block-foot">
          <button className="ev-more-btn" onClick={onShowMore}>Загрузить ещё события</button>
        </div>
      )}
    </div>
  );
}

// ─── Link sentence ────────────────────────────────────────────────────────────

function LinkSentence({ evs, filters, metric }: { evs: EvRow[]; filters: Record<Ax, Set<string>>; metric: Metric }) {
  if (!evs.length) return <div className="eg-link-sentence eg-muted">Нет событий под фильтр за период.</div>;
  const axes: Ax[] = ['c', 'p', 'g'];
  const selParts: string[] = [];
  axes.forEach(ax => [...filters[ax]].forEach(k => {
    selParts.push(`<span class="eg-hl" style="background:${nodeColor(ax, k)}">${k}</span>`);
  }));
  const lines: string[] = [];
  axes.forEach(ax => {
    if (filters[ax].size) return;
    const cnt: Record<string, number> = {};
    evs.forEach(e => e[ax].forEach(k => { cnt[k] = (cnt[k] || 0) + 1; }));
    const top = Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a]).slice(0, 2);
    if (!top.length) return;
    const frag = top.map(k =>
      `<span class="eg-hl" style="background:${nodeColor(ax, k)}">${k}</span> <span class="eg-pct">${Math.round(cnt[k] / evs.length * 100)}%</span>`
    ).join(' и ');
    lines.push(`по оси «${AXNAME[ax]}» — ${frag}`);
  });
  const html = `Выбрано: ${selParts.join(' + ')} <span class="eg-muted">(${evs.length} соб.)</span>.${lines.length ? '<br>Связано: ' + lines.join('; ') + '.' : ''}`;
  // eslint-disable-next-line react/no-danger
  return <div className="eg-link-sentence" dangerouslySetInnerHTML={{ __html: html }} />;
}

// ─── Main Component ───────────────────────────────────────────────────────────

type Props = {
  events: EventItem[];
  graphData?: EventGraphItem[];
  total: number;
  loading?: boolean;
  role: Filters['role'];
  onOpenNews: (id: number) => void;
  onTagClick: (tag: string) => void;
  selectedPeriodLabel?: string | null;
  isPeriodSelected?: boolean;
  onOpenAllEvents?: () => void;
  fullPage?: boolean;
  hasMore?: boolean;
  onShowMore?: () => void;
  onSelectRange?: (dateFrom: string, dateTo: string, options?: { scroll?: boolean }) => void;
  selectedDateFrom?: string | null;
  selectedDateTo?: string | null;
  onClearRange?: () => void;
  hideEventsBlock?: boolean;
  // Graph ↔ App.tsx filter sync
  activeTopics?: string[];
  activeRegion?: string | null;
  activeProduct?: string | null;
  onGraphTopicToggle?: (topic: string) => void;
  onGraphRegionToggle?: (region: string) => void;
  onGraphProductToggle?: (product: string) => void;
  onClearGraphFilters?: () => void;
  onOpenFilters?: () => void;       // открыть окно фильтров (на месте бывшей шестерёнки)
  filterCount?: number;             // число активных фильтров (бейдж)
};

export function EventIntelligence({
  events, graphData, total, loading, fullPage,
  onOpenAllEvents, hasMore, onShowMore,
  onSelectRange, selectedDateFrom, selectedDateTo, onClearRange,
  hideEventsBlock,
  role,
  onOpenNews, onTagClick,
  activeTopics, activeRegion, activeProduct,
  onGraphTopicToggle, onGraphRegionToggle, onGraphProductToggle, onClearGraphFilters,
  onOpenFilters, filterCount,
}: Props) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const svgRef = useRef<SVGSVGElement>(null);
  const [graphInfo, setGraphInfo] = useState<GraphInfo | null>(null);
  const [tipState, setTipState] = useState<{ x: number; y: number; html: string } | null>(null);
  const [compact, setCompact] = useState(() => typeof window !== 'undefined' && window.innerWidth <= 700);

  const tip = useMemo(() => ({
    show: (x: number, y: number, html: string) => setTipState({ x, y, html }),
    hide: () => setTipState(null),
  }), []);

  // When App.tsx callbacks provided → node click goes to App.tsx filters (and back-syncs for highlight).
  // Without callbacks (e.g. standalone events page) → pure internal state as before.
  const hasExternalSync = Boolean(onGraphTopicToggle || onGraphRegionToggle || onGraphProductToggle);

  const handleToggle = useCallback((ax: Ax, k: string) => {
    if (hasExternalSync) {
      if (ax === 'c') onGraphTopicToggle?.(k);
      else if (ax === 'p') onGraphProductToggle?.(k);
      else if (ax === 'g') onGraphRegionToggle?.(k);
    } else {
      dispatch({ type: 'TOGGLE_FILTER', ax, k });
    }
  }, [hasExternalSync, onGraphTopicToggle, onGraphProductToggle, onGraphRegionToggle]);

  // External filters (FilterBar/FilterDrawer) → sync into state.filters for node highlighting
  const prevExternalKey = useRef('');
  useEffect(() => {
    if (!hasExternalSync) return;
    const key = [
      [...(activeTopics ?? [])].sort().join('|'),
      activeRegion ?? '',
      activeProduct ?? '',
    ].join('\x00');
    if (key === prevExternalKey.current) return;
    prevExternalKey.current = key;
    dispatch({
      type: 'SET_FILTERS',
      filters: {
        c: new Set(activeTopics ?? []),
        p: activeProduct ? new Set([activeProduct]) : new Set(),
        g: activeRegion ? new Set([activeRegion]) : new Set(),
      },
    });
  }, [hasExternalSync, activeTopics, activeRegion, activeProduct]);

  const { rows, newestDate } = useMemo(() => buildEvRows(graphData ?? events), [graphData, events]);

  // Events filtered by selected buckets + node filters, for graph rendering
  const visibleEvs = useMemo(() => {
    return rows.filter(e => {
      if (state.selectedBuckets.size > 0) {
        const key = getBucketKey(e.dateFrom, state.scale);
        if (!state.selectedBuckets.has(key)) return false;
      }
      return !anyFilter(state.filters) || passFilter(e, null, state.filters);
    });
  }, [rows, state.scale, state.selectedBuckets, state.filters]);

  const renderGraph = useRenderGraph(svgRef, visibleEvs, state, dispatch, tip, handleToggle);

  useEffect(() => {
    const info = renderGraph();
    if (info) setGraphInfo(info);
  }, [renderGraph]);

  useEffect(() => {
    let rz: ReturnType<typeof setTimeout>;
    const onResize = () => {
      clearTimeout(rz);
      rz = setTimeout(() => {
        setCompact(window.innerWidth <= 700);
        renderGraph();
      }, 200);
    };
    window.addEventListener('resize', onResize);
    return () => { clearTimeout(rz); window.removeEventListener('resize', onResize); };
  }, [renderGraph]);

  // Sync bucket selection → global date filter
  const prevBucketsKey = useRef<string>('');
  useEffect(() => {
    const buckets = state.selectedBuckets;
    const key = [...buckets].sort().join(',');
    if (key === prevBucketsKey.current) return;
    prevBucketsKey.current = key;
    if (!newestDate) return;
    if (buckets.size === 0) { onClearRange?.(); return; }
    // Compute total span of all selected buckets
    let minFrom = '9999-12-31', maxTo = '0000-01-01';
    buckets.forEach(bk => {
      const [from, to] = getBucketDateRange(bk, state.scale);
      if (from < minFrom) minFrom = from;
      if (to > maxTo) maxTo = to;
    });
    onSelectRange?.(minFrom, maxTo, { scroll: false });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.selectedBuckets, state.scale, newestDate]);

  const hasFilters = anyFilter(state.filters);
  const axes: Ax[] = ['c', 'p', 'g'];
  const AX_LBL: Record<Ax, string> = { c: 'ТЕМА', p: 'ПРОДУКТ', g: 'ГЕОГРАФИЯ' };
  // Column order mirrors the graph: center axis sits in the middle
  const colOrder: [Ax, Ax, Ax] =
    state.center === 'c' ? ['p', 'c', 'g'] : state.center === 'g' ? ['c', 'g', 'p'] : ['c', 'p', 'g'];

  if (loading && !events.length) {
    return (
      <section className={`eg-block${fullPage ? ' eg-page-block' : ''}`}>
        <div className="eg-hd">
          <div className="eg-hd-l"><h2 className="eg-hd-title">Активность рынка</h2></div>
        </div>
        <div className="events-skeleton"><span /><span /></div>
      </section>
    );
  }

  if (!events.length) {
    return (
      <section className={`eg-block${fullPage ? ' eg-page-block' : ''}`}>
        <div className="eg-hd">
          <div className="eg-hd-l">
            <div className="events-k">события</div>
            <h2 className="eg-hd-title">Событий пока нет</h2>
            <p style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
              Фоновый worker ещё не подготовил события.
            </p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className={`eg-block${fullPage ? ' eg-page-block' : ''}`}>
      {/* Header */}
      <div className="eg-hd">
        <div className="eg-hd-l">
          <h2 className="eg-hd-title">Активность рынка</h2>
          <div className="eg-hd-info">
            <span className="num">{graphInfo?.evCount ?? total}</span> событий
            {graphInfo?.topLink && graphInfo.topLink !== '—' && (
              <>
                <span className="eg-dot" />
                {graphInfo.topLabel}
                <span className="eg-em">{graphInfo.topLink}</span>
              </>
            )}
            {selectedDateFrom && selectedDateTo && (
              <>
                <span className="eg-dot" />
                <span className="eg-em">{rangeLabel(selectedDateFrom, selectedDateTo)}</span>
                <button className="tl2-clr" onClick={() => { dispatch({ type: 'CLEAR_BUCKETS' }); onClearRange?.(); }}>× сбросить</button>
              </>
            )}
          </div>
        </div>
        <div className="eg-hd-r">
          {compact ? (
            <>
              {/* Мобилка: 2 кнопки-переключателя (показывают текущее, тап = переключить) */}
              <button className="eg-toggle-btn" onClick={() => dispatch({ type: 'SET_METRIC', metric: state.metric === 'freq' ? 'lift' : 'freq' })}>
                {state.metric === 'freq' ? 'частые' : 'характерные'}
              </button>
              <button className="eg-toggle-btn" onClick={() => dispatch({ type: 'SET_SCALE', scale: state.scale === 'week' ? 'month' : 'week' })}>
                {state.scale === 'week' ? 'неделя' : 'месяц'}
              </button>
            </>
          ) : (
            <>
              <div className="eg-metric-grp eg-metric-grp-inline">
                {(['freq', 'lift'] as Metric[]).map(m => (
                  <button key={m} className={`eg-metric-btn${state.metric === m ? ' on' : ''}`}
                    onClick={() => dispatch({ type: 'SET_METRIC', metric: m })}>
                    {m === 'freq' ? 'частые' : 'характерные'}
                  </button>
                ))}
              </div>
              <div className="eg-scale-tabs">
                {(['week', 'month'] as Scale[]).map(s => (
                  <button key={s} className={`eg-scale-tab${state.scale === s ? ' on' : ''}`}
                    onClick={() => dispatch({ type: 'SET_SCALE', scale: s })}>
                    {SCALE_LABELS[s]}
                  </button>
                ))}
              </div>
            </>
          )}
          {onOpenFilters && (
            <button className="eg-filters-btn" onClick={onOpenFilters} aria-label="Фильтры" title="Фильтры">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/>
              </svg>
              <span className="eg-filters-label">Фильтры</span>
              {(filterCount ?? 0) > 0 && <span className="eg-filters-c">{filterCount}</span>}
            </button>
          )}
        </div>
      </div>

      {/* Active filters */}
      {hasFilters && (
        <div className="eg-afilters">
          <span className="eg-af-lbl">фильтр:</span>
          <span className="eg-chips">
            {axes.map(ax =>
              [...state.filters[ax]].map(k => (
                <span key={`${ax}|${k}`} className="eg-chip">
                  <span className="eg-chip-dot" style={{ background: nodeColor(ax, k) }} />
                  {k}
                  <button onClick={() => handleToggle(ax, k)}>
                    <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round">
                      <path d="M18 6L6 18M6 6l12 12"/>
                    </svg>
                  </button>
                </span>
              ))
            )}
          </span>
          <button className="eg-af-clear" onClick={() => {
            if (onClearGraphFilters) onClearGraphFilters();
            else dispatch({ type: 'CLEAR_FILTERS' });
          }}>сбросить</button>
        </div>
      )}

      {/* Mini timeline */}
      <MiniTimeline rows={rows} state={state} dispatch={dispatch} />

      {/* Column headers + inline expanders (axis label clickable to rotate center) */}
      <div className="eg-colhd-row">
        {colOrder.map((ax, ci) => {
          const mid = ci === 1;
          const tot = graphInfo?.totals[ax] ?? 0;
          const showExp = !state.filters[ax].size && tot > topnFor(compact);
          return (
            <div key={ax} className="eg-colhd">
              <button
                className={`eg-colhd-ax${mid ? ' on' : ''}`}
                disabled={mid}
                onClick={() => { if (!mid) dispatch({ type: 'SET_CENTER', ax }); }}
                title={mid ? 'Центральная ось' : `Сделать «${AX_LBL[ax]}» центром`}
              >
                {AX_LBL[ax]}
              </button>
              {showExp && (
                <button
                  className={`eg-exp-btn${state.expanded[ax] ? ' open' : ''}`}
                  onClick={() => dispatch({ type: 'TOGGLE_EXPANDED', ax })}
                >
                  {state.expanded[ax] ? 'свернуть' : '+ ещё'}
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                    <path d="M6 9l6 6 6-6"/>
                  </svg>
                </button>
              )}
            </div>
          );
        })}
      </div>

      {/* SVG graph */}
      <div className="eg-graph-wrap">
        <svg className="eg-graph" ref={svgRef} preserveAspectRatio="xMidYMid meet" />
      </div>

      {/* Link sentence */}
      {hasFilters && <LinkSentence evs={visibleEvs} filters={state.filters} metric={state.metric} />}

      {/* Metric hint */}
      {state.metric === 'lift' && (
        <div className="eg-metric-hint">
          <b>Неожиданные связи:</b> толщина = во сколько раз пара встречается вместе чаще случайного.
          Специфичные связки — то, что обычная лента пропускает.
        </div>
      )}

      {/* Events block — hidden when parent renders it separately */}
      {!hideEventsBlock && (
        <EventsBlock
          events={events}
          total={total}
          loading={loading}
          role={role}
          hasMore={hasMore}
          onShowMore={onShowMore}
          onOpenAllEvents={onOpenAllEvents}
          onOpenNews={onOpenNews}
          onTagClick={onTagClick}
        />
      )}

      {/* Tooltip */}
      {tipState && (
        <div
          className="eg-tip"
          style={{ left: Math.min(tipState.x + 14, window.innerWidth - 260), top: tipState.y - 10 }}
          dangerouslySetInnerHTML={{ __html: tipState.html }}
        />
      )}

    </section>
  );
}
