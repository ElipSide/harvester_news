import type { NewsItem } from '../types';
import { formatDate } from '../utils/format';
import { topicClass } from '../utils/topic';
import { NewsImage } from './NewsImage';

const API_URL = (import.meta.env.VITE_API_URL || `${(import.meta.env.BASE_URL || '/').replace(/\/$/, '')}/api/v1`);

export function Featured({ items, onOpenNews }: { items: NewsItem[]; onOpenNews: (id: number) => void }) {
  const photoItems = items.slice(0, 3);
  if (!photoItems.length) return null;
  return (
    <section className="feat">
      {photoItems.map((item, idx) => {
        const topic = item.topics[0] || null;
        const cardSrc = `${API_URL}/news/${item.id}/card.png`;
        return (
          <button className="cov" key={item.id} onClick={() => onOpenNews(item.id)}>
            <NewsImage
              src={item.link_photo || null}
              fallbackSrc={cardSrc}
              alt={item.title}
              fallbackClassName="cov-fallback"
            />
            <div className="cov-grad" />
            {topic && <span className={`cov-tag ${topicClass(topic)}`}>{topic}</span>}
            {idx === 0 && <span className="cov-feat">главное</span>}
            {/* Ранг: показывается только в компактной (мобильной) раскладке через CSS */}
            <span className="cov-rank">№{idx + 1}</span>
            <div className="cov-c">
              <div className="cov-meta">{formatDate(item.date)} · {item.source || 'источник не указан'}</div>
              <h2 className="cov-ttl">{item.title}</h2>
            </div>
          </button>
        );
      })}
    </section>
  );
}
