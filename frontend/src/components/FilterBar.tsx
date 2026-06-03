import { useMemo, useState, type Ref } from 'react';
import { CalendarDays, ChevronDown, ChevronUp, Search, X } from 'lucide-react';
import type { FacetItem, Filters } from '../types';

const COLLAPSED_TOPICS_COUNT = 32;
const SHOW_TOPICS_BLOCK = false;

function uniqueFacetList(items: FacetItem[]): FacetItem[] {
  const seen = new Set<string>();
  const result: FacetItem[] = [];
  for (const item of items) {
    if (!item.name || seen.has(item.name)) continue;
    seen.add(item.name);
    result.push(item);
  }
  return result;
}

export function FilterBar({
  filters,
  topics,
  total,
  onToggleTopic,
  onResetTopics,
  onChange,
  searchInputRef,
  searchOpen = false,
  selectedPeriodLabel,
  onClearTimelineRange,
}: {
  filters: Filters;
  topics: FacetItem[];
  tags: FacetItem[];
  total: number;
  onToggleTopic: (topic: string) => void;
  onResetTopics: () => void;
  onToggleTag: (tag: string) => void;
  onOpenFilters: () => void;
  onChange: (patch: Partial<Filters>) => void;
  searchInputRef: Ref<HTMLInputElement>;
  searchOpen?: boolean;
  selectedPeriodLabel: string | null;
  onClearTimelineRange: () => void;
}) {
  const [topicsExpanded, setTopicsExpanded] = useState(false);

  const visibleTopics = useMemo(() => {
    if (topicsExpanded) return topics;

    const topicByName = new Map(topics.map((topic) => [topic.name, topic]));
    const selected = filters.topics.map((name) => topicByName.get(name) || { name, count: 0 });
    return uniqueFacetList([...selected, ...topics.slice(0, COLLAPSED_TOPICS_COUNT)]);
  }, [filters.topics, topics, topicsExpanded]);

  const hiddenTopicsCount = Math.max(0, topics.length - visibleTopics.length);

  return (
    <div className={`filter-panel single-tags-panel${searchOpen ? ' search-open' : ''}`}>
      <div className="filter-bar compact-filter-bar">
        <div className="filter-search">
          <Search />
          <input
            ref={searchInputRef}
            value={filters.q}
            onChange={(event) => onChange({ q: event.target.value })}
            placeholder="Поиск по заголовку, тексту, источнику"
            aria-label="Поиск по новостям"
          />
          {filters.q && <button className="filter-search-clear" onClick={() => onChange({ q: '' })} aria-label="Очистить поиск"><X /></button>}
        </div>
      </div>

      {selectedPeriodLabel && (
        <div className="selected-period-strip">
          <span className="selected-period-pill"><CalendarDays /> график: {selectedPeriodLabel}</span>
          <span className="selected-period-note">новости ниже отфильтрованы по выбранному периоду</span>
          <button className="selected-period-clear" onClick={onClearTimelineRange}><X /> Сбросить период</button>
        </div>
      )}

      {SHOW_TOPICS_BLOCK && <div className={`tag-cloud-panel main-tags-block ${topicsExpanded ? 'expanded' : ''}`}>
        <div className="tag-cloud-head">
          <div>
            <span className="tag-cloud-k">темы</span>
          </div>
          <button className="tag-cloud-toggle" onClick={() => setTopicsExpanded((value) => !value)}>
            {topicsExpanded ? <ChevronUp /> : <ChevronDown />}
            {topicsExpanded ? 'Свернуть' : `Показать все${hiddenTopicsCount ? ` · ещё ${hiddenTopicsCount}` : ''}`}
          </button>
        </div>
        <div className="tag-cloud-body">
          <button className={`pretty-tag tag-all ${filters.topics.length === 0 ? 'selected' : ''}`} onClick={onResetTopics} title="Показать все новости">
            <span>Все</span>
            <em>{total}</em>
          </button>
          {visibleTopics.map((topic) => {
            const active = filters.topics.includes(topic.name);
            return (
              <button key={topic.name} className={`pretty-tag ${active ? 'selected' : ''}`} onClick={() => onToggleTopic(topic.name)} title={active ? 'Убрать тему из фильтра' : 'Добавить тему в фильтр'}>
                <span>{topic.name}</span>
                <em>{topic.count}</em>
              </button>
            );
          })}
        </div>
      </div>}
    </div>
  );
}
