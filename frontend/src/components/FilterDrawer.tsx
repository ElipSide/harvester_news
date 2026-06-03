import { Check, X } from 'lucide-react';
import type { FacetItem, Filters } from '../types';

// Федеральные округа — локальный фокус ленты/графа по региону.
const DISTRICTS = ['ЦФО', 'ЮФО', 'СКФО', 'ПФО', 'УФО', 'СФО', 'ДФО', 'СЗФО'];

type RoleKey = NonNullable<Filters['role']>;

type RolePreset = {
  key: RoleKey;
  icon: string;
  title: string;
  subtitle: string;
  maxName: string;
  maxMeta: string;
  personTitle: string;
  personMeta: string;
  period: Filters['period'];
  sort: Filters['sort'];
  topicKeywords: string[];
  productKeywords: string[];
  regionKeywords: string[];
  hasPhoto: boolean | null;
};

const rolePresets: RolePreset[] = [
  {
    key: 'farmer',
    icon: '🌾',
    title: 'Фермер',
    subtitle: 'цены · погода · урожай',
    maxName: 'Фермеры юга РФ',
    maxMeta: '12.4к участников · канал в MAX',
    personTitle: 'Фермер · Воронеж',
    personMeta: 'пшеница · семена · риски урожая',
    period: null,
    sort: 'date_desc',
    topicKeywords: ['пшеница', 'семена', 'засуха', 'цена', 'урожай'],
    productKeywords: ['пшеница', 'зерно'],
    regionKeywords: ['воронеж', 'цфо', 'централь'],
    hasPhoto: null,
  },
  {
    key: 'processor',
    icon: '🏭',
    title: 'Переработчик',
    subtitle: 'сырьё · качество · маржа',
    maxName: 'Переработка зерна',
    maxMeta: '4.8к участников · канал в MAX',
    personTitle: 'Переработчик · сырьё',
    personMeta: 'закупка · качество · поставки',
    period: null,
    sort: 'views_desc',
    topicKeywords: ['переработка', 'масло', 'соя', 'качество', 'цена'],
    productKeywords: ['соя', 'масло', 'пшеница'],
    regionKeywords: ['цфо', 'пфо', 'приволж'],
    hasPhoto: null,
  },
  {
    key: 'trader',
    icon: '💼',
    title: 'Трейдер',
    subtitle: 'арбитраж · сделки · спрос',
    maxName: 'Зерновые трейдеры РФ',
    maxMeta: '8.6к участников · канал в MAX',
    personTitle: 'Трейдер · РФ',
    personMeta: 'экспорт · спрос · цены',
    period: null,
    sort: 'views_desc',
    topicKeywords: ['торговля', 'экспорт', 'сделки', 'цена', 'порт'],
    productKeywords: ['пшеница', 'зерно', 'кукуруза'],
    regionKeywords: ['россия', 'юфо', 'цфо'],
    hasPhoto: null,
  },
  {
    key: 'agroholding',
    icon: '🏢',
    title: 'Агрохолдинг',
    subtitle: 'портфель · регионы · регуляторика',
    maxName: 'Топ-30 агрохолдингов',
    maxMeta: '1.2к участников · канал в MAX',
    personTitle: 'Агрохолдинг · портфель',
    personMeta: 'регионы · техника · регулирование',
    period: null,
    sort: 'views_desc',
    topicKeywords: ['агрохолдинги', 'регулирование', 'технологии', 'аналитика', 'субсидии'],
    productKeywords: ['зерно', 'пшеница', 'соя'],
    regionKeywords: ['россия', 'цфо', 'юфо'],
    hasPhoto: null,
  },
  {
    key: 'exporter',
    icon: '🚢',
    title: 'Экспортёр',
    subtitle: 'порты · netback · логистика',
    maxName: 'Экспортёры зерна',
    maxMeta: '2.3к участников · канал в MAX',
    personTitle: 'Экспортёр · портовый базис',
    personMeta: 'экспорт · порты · логистика',
    period: null,
    sort: 'views_desc',
    topicKeywords: ['экспорт', 'порт', 'логистика', 'азия', 'импорт'],
    productKeywords: ['пшеница', 'кукуруза', 'зерно'],
    regionKeywords: ['юфо', 'сзфо', 'дфо'],
    hasPhoto: null,
  },
];


function matchesKeyword(value: string, keywords: string[]) {
  const normal = value.toLowerCase();
  return keywords.some((keyword) => normal.includes(keyword.toLowerCase()));
}

function firstMatchingFacet(items: FacetItem[], keywords: string[]): string | null {
  return items.find((item) => matchesKeyword(item.name, keywords))?.name || null;
}

function matchingTopics(topics: FacetItem[], keywords: string[]): string[] {
  const result: string[] = [];
  for (const keyword of keywords) {
    const hit = topics.find((topic) => matchesKeyword(topic.name, [keyword]));
    if (hit && !result.includes(hit.name)) result.push(hit.name);
  }
  return result.slice(0, 3);
}

export function FilterDrawer({
  open,
  filters,
  regions,
  products,
  topics,
  onClose,
  onChange,
  onReset,
  resultCount,
}: {
  open: boolean;
  filters: Filters;
  regions: FacetItem[];
  products: FacetItem[];
  topics: FacetItem[];
  onClose: () => void;
  onChange: (patch: Partial<Filters>) => void;
  onToggleTopic: (topic: string) => void;
  onReset: () => void;
  resultCount: number;
}) {
  const applyRole = (role: RolePreset) => {
    const roleTopics = matchingTopics(topics, role.topicKeywords);
    const product = firstMatchingFacet(products, role.productKeywords);
    const region = firstMatchingFacet(regions, role.regionKeywords);

    onChange({
      role: role.key,
      period: role.period,
      topics: roleTopics,
      tags: [],
      product,
      region,
      sort: role.sort,
      hasPhoto: role.hasPhoto,
      dateFrom: null,
      dateTo: null,
    });
  };

  return (
    <>
      <div className={`drawer-bg ${open ? 'on' : ''}`} onClick={onClose} />
      <aside className={`drawer ${open ? 'on' : ''}`}>
        <div className="drawer-hd">
          <h2 className="drawer-ttl">Фильтры</h2>
          <button className="drawer-close" onClick={onClose} aria-label="Закрыть"><X /></button>
        </div>
        <div className="drawer-body">
          <div className="drawer-section">
            <div className="drawer-section-k">профиль · подобрать новости под роль</div>
            <p className="drawer-hint">Пресет меняет акцент: поднимает темы, продукты и регионы роли в графе и ленте, меняет сортировку. Ничего не скрывает — все события остаются.</p>
            <div className="seg-grid">
              {rolePresets.map((role) => {
                const active = filters.role === role.key;
                return (
                  <button key={role.key} className={`seg-card ${active ? 'on' : ''}`} onClick={() => applyRole(role)}>
                    <span className="seg-card-ic">{role.icon}</span>
                    <span className="seg-card-tx">
                      <span className="seg-card-t">{role.title}</span>
                      <span className="seg-card-m">{role.subtitle}</span>
                    </span>
                    <span className="seg-card-ck"><Check /></span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="drawer-section">
            <div className="drawer-section-k">федеральный округ</div>
            <p className="drawer-hint">Локальный фокус: поднимает события округа и его специфику (что там выращивают).</p>
            <div className="dr-chips">
              {DISTRICTS.map((d) => (
                <button
                  key={d}
                  className={`dr-chip ${filters.region === d ? 'on' : ''}`}
                  onClick={() => onChange({ region: filters.region === d ? null : d })}
                >
                  {d}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="drawer-foot">
          <button className="drawer-reset" onClick={onReset}>Сбросить</button>
          <button className="drawer-apply" onClick={onClose}>Показать {resultCount} новостей</button>
        </div>
      </aside>
    </>
  );
}
