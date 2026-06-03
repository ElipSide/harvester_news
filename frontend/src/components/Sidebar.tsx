import type { NewsItem } from '../types';
import { formatNumber } from '../utils/format';

export function Sidebar({ topRead, onOpenNews }: { topRead: NewsItem[]; onOpenNews: (id: number) => void }) {
  if (!topRead.length) return null;
  return (
    <aside className="side">
      <div className="wgt">
        <div className="wgt-hd"><span className="wgt-hd-l">Читают сейчас</span></div>
        <div className="wgt-list">
          {topRead.map((item, idx) => (
            <button key={item.id} className="top-row" onClick={() => onOpenNews(item.id)}>
              <span className="top-row-n">{idx + 1}</span>
              <span className="top-row-c">
                <span className="top-row-t">{item.title}</span>
                <span className="top-row-m">{item.topics[0] || 'новость'} · {formatNumber(item.views)} чтений</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </aside>
  );
}
