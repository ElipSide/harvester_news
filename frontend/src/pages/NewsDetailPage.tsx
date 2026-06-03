import { useEffect, useState } from 'react';
import { ArrowLeft, ChevronDown, ChevronLeft, ExternalLink } from 'lucide-react';
import type { EventSource, FullGraphResponse, NewsItem } from '../types';
import { api } from '../api/client';
import { formatDate } from '../utils/format';
import { topicClass } from '../utils/topic';
import { cleanSummary } from '../utils/text';
import { NewsImage } from '../components/NewsImage';
import { RichText } from '../components/RichText';
import { StoryTimeline } from '../components/StoryTimeline';

// Дата в формате примера: ДД.ММ.ГГГГ
const ddmmyyyy = (raw: string) => {
  const d = new Date(raw && raw.length <= 10 ? `${raw}T00:00:00` : raw);
  if (Number.isNaN(d.getTime())) return raw;
  return `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}.${d.getFullYear()}`;
};

// Время чтения: ~180 слов/мин (русский текст), минимум 1 мин.
const readMinutes = (text: string | null | undefined): number => {
  const words = (text || '').trim().split(/\s+/).filter(Boolean).length;
  return Math.max(1, Math.round(words / 180));
};

/**
 * Telegram-посты часто начинаются с заголовка жирным («*Заголовок.*»). Срезаем
 * первую строку текста, если она дублирует заголовок статьи.
 */
function withoutLeadingTitle(text: string | null | undefined, title: string): string | null {
  if (!text) return null;
  const normalize = (s: string) =>
    s.replace(/\*([^*]+)\*/g, '$1').replace(/[.,!?;:…«»""'']/g, '').replace(/\s+/g, ' ').toLowerCase().trim();
  const normTitle = normalize(title);
  if (!normTitle) return text;
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const firstIdx = lines.findIndex((l) => l.trim());
  if (firstIdx === -1) return text;
  const firstLine = normalize(lines[firstIdx]);
  if (firstLine === normTitle || normTitle.includes(firstLine) || firstLine.includes(normTitle)) {
    lines.splice(firstIdx, 1);
    return lines.join('\n').replace(/^\n+/, '');
  }
  return text;
}

function SimilarCard({ item, onOpen }: { item: NewsItem; onOpen: (id: number) => void }) {
  const topic = item.topics[0] || null;
  return (
    <button className="sim-card" onClick={() => onOpen(item.id)}>
      <div className="sim-img-wrap">
        <NewsImage src={item.link_photo} alt={item.title} className="sim-img" fallbackClassName="sim-img-fallback" />
      </div>
      <div className="sim-body">
        {topic && <div className={`sim-topic ${topicClass(topic)}`}>{topic}</div>}
        <div className="sim-title">{item.title}</div>
        <div className="sim-meta">{formatDate(item.date)}{item.source ? ` · ${item.source}` : ''}</div>
      </div>
    </button>
  );
}

export function NewsDetailPage({
  item, loading, error, onBack, onTagClick, onOpenNews,
}: {
  item: NewsItem | null;
  loading: boolean;
  error: string | null;
  onBack: () => void;
  onTagClick: (tag: string) => void;
  onOpenNews: (id: number) => void;
}) {
  const SHOW_SIMILAR = false;
  // Полный текст новости скрыт — страница чтения 1:1 как в примере (только событие + граф).
  // Вернуть = true (разметка/стили остаются на месте).
  const SHOW_FULLTEXT = false;
  const [similar, setSimilar] = useState<NewsItem[]>([]);
  const [graph, setGraph] = useState<FullGraphResponse | null>(null);
  // Выпадашка источников события (ленивая загрузка по клику на «N ист.»).
  const [srcOpen, setSrcOpen] = useState(false);
  const [sources, setSources] = useState<EventSource[] | null>(null);
  const [srcLoading, setSrcLoading] = useState(false);

  useEffect(() => {
    setSimilar([]);
    if (!SHOW_SIMILAR || !item) return;
    api.getSimilarNews(item.id, 3).then(setSimilar).catch(() => {});
  }, [item?.id]);

  // Полный граф событий: даёт σ/ист./dek фокус-события и питает таймлайн (один запрос).
  useEffect(() => {
    setGraph(null);
    setSrcOpen(false);
    setSources(null);
    if (!item) return;
    let cancelled = false;
    api.getEventsFullGraph(item.id).then((g) => { if (!cancelled) setGraph(g); }).catch(() => {});
    return () => { cancelled = true; };
  }, [item?.id]);

  const toggleSources = (eventId: number) => {
    setSrcOpen((open) => {
      const next = !open;
      if (next && sources === null && !srcLoading) {
        setSrcLoading(true);
        api.getEventSources(eventId)
          .then((r) => setSources(r.items))
          .catch(() => setSources([]))
          .finally(() => setSrcLoading(false));
      }
      return next;
    });
  };

  if (loading) {
    return (
      <main className="page detail-page">
        <button className="back-link" onClick={onBack}><ArrowLeft /> К ленте</button>
        <div className="empty-state">Загрузка новости…</div>
      </main>
    );
  }

  if (error || !item) {
    return (
      <main className="page detail-page">
        <button className="back-link" onClick={onBack}><ArrowLeft /> К ленте</button>
        <div className="error-box">{error || 'Новость не найдена'}</div>
      </main>
    );
  }

  const hasTags = item.topics.length > 0 || item.regions.length > 0 || item.products.length > 0;
  const focusEvent = graph && graph.focus_event_id != null
    ? graph.nodes.find((n) => n.id === graph.focus_event_id) || null
    : null;
  const dek = cleanSummary(focusEvent?.dek || item.summary || item.text || '');
  const bodyText = withoutLeadingTitle(item.text, item.title);
  const readMin = readMinutes(item.text || item.summary);

  return (
    <main className="page detail-page event-page">
      {/* Карточка-событие — оформление как в примере */}
      <article className="ev-card2">
        <div className="ev2-head">
          <button className="ev2-back" onClick={onBack} aria-label="Назад к ленте" title="Назад к ленте"><ChevronLeft /></button>
          <h1>{item.title}</h1>
        </div>

        <div className="ev2-meta">
          {focusEvent
            ? (
              <>
                <span className="ev2-mm">{ddmmyyyy(item.date)}</span>
                <span className="ev2-src-wrap">
                  <button
                    type="button"
                    className={`ev2-mm ev2-src-btn${srcOpen ? ' open' : ''}`}
                    onClick={() => toggleSources(focusEvent.id)}
                    aria-expanded={srcOpen}
                    title="Показать источники"
                  >
                    {focusEvent.src} ист.
                    <ChevronDown className="ev2-src-caret" />
                  </button>
                  {srcOpen && (
                    <div className="ev2-src-pop">
                      {srcLoading && <div className="ev2-src-empty">Загрузка…</div>}
                      {!srcLoading && sources && sources.length === 0 && (
                        <div className="ev2-src-empty">Источники не найдены</div>
                      )}
                      {!srcLoading && sources && sources.map((s) => {
                        const inner = (
                          <>
                            <span className="ev2-src-name">{s.source || s.title || 'Источник'}</span>
                            <span className="ev2-src-meta">
                              {s.date ? ddmmyyyy(s.date) : ''}
                              {s.link_site && <ExternalLink className="ev2-src-ext" />}
                            </span>
                          </>
                        );
                        return s.link_site
                          ? (
                            <a key={s.id} className="ev2-src-row" href={s.link_site} target="_blank" rel="noreferrer">{inner}</a>
                          )
                          : (
                            <button key={s.id} type="button" className="ev2-src-row" onClick={() => { setSrcOpen(false); onOpenNews(s.id); }}>{inner}</button>
                          );
                      })}
                    </div>
                  )}
                </span>
                <span className="ev2-mm">{readMin} мин чтения</span>
              </>
            )
            : (
              <>
                <span className="ev2-mm">{ddmmyyyy(item.date)}</span>
                {item.source && <span className="ev2-mm">{item.source}</span>}
                <span className="ev2-mm">{readMin} мин чтения</span>
              </>
            )}
        </div>

        {item.link_photo && (
          <div className="ev2-media">
            <NewsImage src={item.link_photo} alt={item.title} className="ev2-img" fallbackClassName="detail-image-fallback" />
          </div>
        )}

        {/* Полный текст новости (не обрезается). Если текста нет — короткая сводка. */}
        {bodyText
          ? <div className="ev2-dek ev2-body"><RichText text={bodyText} fallback={item.summary} /></div>
          : (dek && <div className="ev2-dek">{dek}</div>)}

        {item.link_site && (
          <a className="ev2-source" href={item.link_site} target="_blank" rel="noreferrer">
            Открыть источник <ExternalLink />
          </a>
        )}

        {hasTags && (
          <div className="detail-tags-flat">
            {[
              ...item.regions.map((t) => ({ t, kind: 'region' })),
              ...item.topics.map((t) => ({ t, kind: 'topic' })),
              ...item.products.map((t) => ({ t, kind: 'product' })),
            ].map(({ t, kind }) => (
              <button key={`${kind}:${t}`} className={`ev-tag fc-${kind}`} onClick={() => onTagClick(t)}>{t}</button>
            ))}
          </div>
        )}

        {graph
          ? <StoryTimeline graph={graph} focusEventId={graph.focus_event_id} onOpenNews={onOpenNews} />
          : (
            <section className="stl-wrap">
              <div className="stl-loading"><span className="stl-loading-dot" /> Загрузка графа события…</div>
            </section>
          )}
      </article>

      {/* Полный текст новости */}
      {SHOW_FULLTEXT && (bodyText || item.summary) && (
        <article className="detail-card detail-fulltext">
          <div className="detail-body">
            <RichText text={bodyText} fallback={item.summary} />
          </div>
          {item.link_site && (
            <a className="source-link" href={item.link_site} target="_blank" rel="noreferrer">
              Открыть источник <ExternalLink />
            </a>
          )}
        </article>
      )}

      {SHOW_SIMILAR && similar.length > 0 && (
        <section className="similar-section">
          <h2 className="similar-heading">Похожие новости</h2>
          <div className="similar-grid">
            {similar.map((s) => <SimilarCard key={s.id} item={s} onOpen={onOpenNews} />)}
          </div>
        </section>
      )}
    </main>
  );
}
