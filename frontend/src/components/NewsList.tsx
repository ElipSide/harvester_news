import { memo } from 'react';
import { Eye, Hash, TrendingDown, TrendingUp } from 'lucide-react';
import type { NewsItem } from '../types';
import { formatDate, formatNumber } from '../utils/format';
import { topicClass } from '../utils/topic';
import { cleanSummary } from '../utils/text';
import { NewsImage } from './NewsImage';

function impactPill(item: NewsItem) {
  const text = `${item.title} ${item.summary}`.toLowerCase();
  if (text.includes('сниж') || text.includes('−') || text.includes('засух') || text.includes('паден')) {
    return <span className="pill neg"><TrendingDown />риск</span>;
  }
  if (text.includes('рост') || text.includes('максим') || text.includes('+') || text.includes('увелич')) {
    return <span className="pill"><TrendingUp />рост</span>;
  }
  return <span className="pill nu">нейтрально</span>;
}

function TopicButton({ topic, onClick }: { topic: string; onClick: (topic: string) => void }) {
  return (
    <button
      className="news-tag-chip fc-topic"
      onClick={(event) => {
        event.stopPropagation();
        onClick(topic);
      }}
      title="Фильтровать по теме"
    >
      <Hash />
      {topic}
    </button>
  );
}

export const NewsList = memo(function NewsList({
  items,
  total,
  onShowMore,
  hasMore,
  onOpenNews,
  onTagClick,
}: {
  items: NewsItem[];
  total: number;
  onShowMore: () => void;
  hasMore: boolean;
  onOpenNews: (id: number) => void;
  onTagClick: (tag: string) => void;
}) {
  return (
    <section className="list">
      {items.map((item) => {
        const topic = item.topics[0] || null;
        const topics = item.topics;
        const hasPhoto = Boolean(item.link_photo);
        return (
          <article className={`li ${hasPhoto ? '' : 'no-thumb'}`} key={item.id} onClick={() => onOpenNews(item.id)}>
            {hasPhoto && (
              <div className="li-thumb">
                <NewsImage src={item.link_photo} alt={item.title} fallbackClassName="li-thumb-fallback" />
                <span className="li-thumb-pip">фото</span>
              </div>
            )}
            <div className="li-c">
              <div className="li-meta">
                {topic && <span className={`li-t ${topicClass(topic)}`}>{topic}</span>}
                {topic && <span className="dot" />}
                <span>{item.source || 'источник не указан'}</span>
                <span className="dot" />
                <span>{formatDate(item.date)}</span>
              </div>
              <h3 className="li-h">{item.title}</h3>
              <p className="li-l">{cleanSummary(item.summary)}</p>
              <div className="li-f">
                {impactPill(item)}
                {item.products.slice(0, 2).map((product) => <span key={product} className="pill prod">{product}</span>)}
                {item.regions.slice(0, 1).map((region) => <span key={region} className="pill reg">{region}</span>)}
              </div>
              {topics.length > 0 && (
                <div className="li-tags">
                  {topics.slice(0, 6).map((topicName) => <TopicButton key={topicName} topic={topicName} onClick={onTagClick} />)}
                  {topics.length > 6 && <span className="li-tags-more">+{topics.length - 6}</span>}
                </div>
              )}
            </div>
            {item.views > 0 && (
              <div className="li-r">
                {item.views > 0 && <span className="li-r-views"><Eye />{formatNumber(item.views)}</span>}
              </div>
            )}
          </article>
        );
      })}
      {!items.length && <div className="empty-state">Новостей по выбранным фильтрам не найдено.</div>}
      {hasMore && <button className="show-more" onClick={onShowMore}>Показать ещё {Math.min(20, total - items.length)}</button>}
    </section>
  );
});
