import { useEffect, useState } from 'react';
import { Activity, AlertTriangle, ArrowLeft, ChevronDown, ChevronLeft, ExternalLink, Eye, TrendingUp } from 'lucide-react';
import type { EventRoleImpact, EventSource, FullGraphResponse, NewsItem } from '../types';
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

// Склонение «новость / новости / новостей».
const newsWord = (n: number): string => {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return 'новость';
  if (m10 >= 2 && m10 <= 4 && !(m100 >= 12 && m100 <= 14)) return 'новости';
  return 'новостей';
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
  // Источники + impacts события (для шапки): один запрос на загрузке страницы.
  const [sources, setSources] = useState<EventSource[] | null>(null);
  const [impacts, setImpacts] = useState<EventRoleImpact[]>([]);
  const [eventArticle, setEventArticle] = useState<string>('');  // статья события (RAGFlow)
  const [eventTitle, setEventTitle] = useState<string>('');      // заголовок события (RAGFlow)
  const [srcOpen, setSrcOpen] = useState(false);          // выпадашка источников
  const [impOpen, setImpOpen] = useState<string | null>(null);  // открытая категория impact
  const [tagsExpanded, setTagsExpanded] = useState(false);      // раскрыты ли все темы

  useEffect(() => {
    setSimilar([]);
    if (!SHOW_SIMILAR || !item) return;
    api.getSimilarNews(item.id, 3).then(setSimilar).catch(() => {});
  }, [item?.id]);

  // Полный граф событий: даёт σ/ист./dek фокус-события и питает таймлайн (один запрос).
  useEffect(() => {
    setGraph(null);
    setSrcOpen(false);
    setImpOpen(null);
    setTagsExpanded(false);
    setSources(null);
    setImpacts([]);
    if (!item) return;
    let cancelled = false;
    api.getEventsFullGraph(item.id).then((g) => { if (!cancelled) setGraph(g); }).catch(() => {});
    return () => { cancelled = true; };
  }, [item?.id]);

  // Источники/impacts фокус-события — когда граф загрузился и нашёл событие.
  const focusEventId = graph && graph.focus_event_id != null ? graph.focus_event_id : null;
  useEffect(() => {
    setSources(null);
    setImpacts([]);
    setEventArticle('');
    setEventTitle('');
    setSrcOpen(false);
    setImpOpen(null);
    if (focusEventId == null) return;
    let cancelled = false;
    api.getEventDetail(focusEventId)
      .then((d) => { if (!cancelled) { setSources(d.sources); setImpacts(d.impacts); setEventArticle((d.article || '').trim()); setEventTitle((d.title || '').trim()); } })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [focusEventId]);

  // Группировка impact-ов по категории для чипов риск/позитив/следить.
  const IMP_CATS = [
    { key: 'negative', word: 'риск', cls: 'neg' },
    { key: 'positive', word: 'позитив', cls: 'pos' },
    { key: 'watch', word: 'следить', cls: 'watch' },
  ] as const;
  const impByCat = (cat: string) => impacts.filter((i) => i.impact === cat);

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
  // Статья/заголовок события: сначала из графа (приходят раньше), затем из event_detail.
  const focusArticle = (graph?.focus_article || eventArticle || '').trim();
  const focusTitleStr = (graph?.focus_title || eventTitle || '').trim();
  // Тело страницы — статья события (RAGFlow), если она есть; иначе текст исходной новости.
  const bodyText = focusArticle || withoutLeadingTitle(item.text, item.title);
  const pageTitle = focusTitleStr || item.title;
  const readMin = readMinutes(focusArticle || item.text || item.summary);
  // Пока граф не загрузился, не показываем короткий текст новости — даём скелетон,
  // чтобы не было «моргания» (короткий текст → полная статья).
  const bodyLoading = graph === null;

  return (
    <main className="page detail-page event-page">
      {/* Карточка-событие — оформление как в примере */}
      <article className="ev-card2">
        <div className="ev2-head">
          <button className="ev2-back" onClick={onBack} aria-label="Назад к ленте" title="Назад к ленте"><ChevronLeft /></button>
          <h1>{pageTitle}</h1>
        </div>

        <div className="ev2-meta">
          {focusEvent ? (
            <>
              {/* Левая группа: дата · время · impact-чипы (клик → выпадашка) */}
              <span className="ev2-mm">{ddmmyyyy(item.date)}</span>
              <span className="ev2-readtime"><span className="ev2-dot">·</span><span className="ev2-mm">{readMin} мин</span></span>
              {IMP_CATS.map((cat) => {
                const roles = impByCat(cat.key);
                if (!roles.length) return null;
                const open = impOpen === cat.key;
                return (
                  <span key={cat.key} className="ev2-dot-wrap">
                    <span className="ev2-dot">·</span>
                    <span className="ev2-imp-wrap">
                      <button
                        type="button"
                        className={`ev2-imp-chip ev2-imp-${cat.cls}${open ? ' open' : ''}`}
                        onClick={() => { setImpOpen(open ? null : cat.key); setSrcOpen(false); }}
                        aria-expanded={open}
                      >
                        {cat.cls === 'neg' && <AlertTriangle />}
                        {cat.cls === 'pos' && <TrendingUp />}
                        {cat.cls === 'watch' && <Activity />}
                        <span className="ev2-imp-word">{cat.word}</span>
                        <b className={`ev2-imp-n${roles.length === 1 ? ' ev2-imp-n-one' : ''}`}>{roles.length}</b>
                        <ChevronDown className="ev2-imp-cv" />
                      </button>
                      {open && (
                        <div className="ev2-imp-pop">
                          <div className="ev2-imp-pop-hd">{cat.word} · {roles.map((r) => r.label).join(', ')}</div>
                          {roles.map((r) => (
                            <div key={r.role} className="ev2-imp-pop-row">
                              <span className="ev2-imp-pop-role">{r.label}</span>
                              {r.summary && <span className="ev2-imp-pop-sm">{r.summary}</span>}
                              {r.action_hint && <span className="ev2-imp-pop-hint">{r.action_hint}</span>}
                            </div>
                          ))}
                        </div>
                      )}
                    </span>
                  </span>
                );
              })}

              {/* Правая группа: просмотры · N новостей (клик → источники) */}
              <span className="ev2-meta-r">
                {item.views > 0 && (
                  <span className="ev2-mm ev2-views"><Eye /> {item.views.toLocaleString('ru-RU')}</span>
                )}
                <span className="ev2-dot">·</span>
                <span className="ev2-src-wrap">
                  <button
                    type="button"
                    className={`ev2-mm ev2-src-btn${srcOpen ? ' open' : ''}`}
                    onClick={() => { setSrcOpen((o) => !o); setImpOpen(null); }}
                    aria-expanded={srcOpen}
                    title="Показать источники"
                  >
                    {focusEvent.src} {newsWord(focusEvent.src)}
                    <ChevronDown className="ev2-src-caret" />
                  </button>
                  {srcOpen && (
                    <div className="ev2-src-pop ev2-src-pop-r">
                      {sources === null && <div className="ev2-src-empty">Загрузка…</div>}
                      {sources && sources.length === 0 && (
                        <div className="ev2-src-empty">Источники не найдены</div>
                      )}
                      {sources && sources.map((s) => {
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
              </span>
            </>
          ) : (
            <>
              <span className="ev2-mm">{ddmmyyyy(item.date)}</span>
              <span className="ev2-readtime"><span className="ev2-dot">·</span><span className="ev2-mm">{readMin} мин</span></span>
              {item.source && <><span className="ev2-dot">·</span><span className="ev2-mm">{item.source}</span></>}
              {item.views > 0 && (
                <span className="ev2-meta-r"><span className="ev2-mm ev2-views"><Eye /> {item.views.toLocaleString('ru-RU')}</span></span>
              )}
            </>
          )}
        </div>

        {item.link_photo && (
          <div className="ev2-media">
            <NewsImage src={item.link_photo} alt={item.title} className="ev2-img" fallbackClassName="detail-image-fallback" />
          </div>
        )}

        {/* Тело: статья события. Пока грузится граф — скелетон (без мелькания короткого текста). */}
        {bodyLoading
          ? (
            <div className="ev2-body ev2-body-skeleton" aria-hidden="true">
              <span className="sk-line" /><span className="sk-line" /><span className="sk-line" />
              <span className="sk-line sk-short" /><span className="sk-line" /><span className="sk-line" />
              <span className="sk-line sk-short" />
            </div>
          )
          : bodyText
            ? <div className="ev2-dek ev2-body"><RichText text={bodyText} fallback={item.summary} /></div>
            : (dek && <div className="ev2-dek">{dek}</div>)}

        {hasTags && (() => {
          const allTags = [
            ...item.regions.map((t) => ({ t, kind: 'region' })),
            ...item.topics.map((t) => ({ t, kind: 'topic' })),
            ...item.products.map((t) => ({ t, kind: 'product' })),
          ];
          const TAG_CAP = 4;
          const shown = tagsExpanded ? allTags : allTags.slice(0, TAG_CAP);
          const hidden = allTags.length - shown.length;
          return (
            <div className="detail-tags-flat">
              {shown.map(({ t, kind }) => (
                <button key={`${kind}:${t}`} className={`ev-tag fc-${kind}`} onClick={() => onTagClick(t)}>{t}</button>
              ))}
              {(hidden > 0 || tagsExpanded) && allTags.length > TAG_CAP && (
                <button type="button" className="ev-tag ev-tag-more" onClick={() => setTagsExpanded((v) => !v)}>
                  {tagsExpanded ? 'свернуть' : `ещё ${hidden}`}
                </button>
              )}
            </div>
          );
        })()}

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
