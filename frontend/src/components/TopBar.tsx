import { FileText, RefreshCw, Search } from 'lucide-react';

export function TopBar({ onNavigate, onSearchClick, onRefresh }: { onNavigate: (path: string) => void; onSearchClick: () => void; onRefresh: () => void }) {
  return (
    <header className="top">
      <button className="top-brand top-brand-btn" onClick={() => onNavigate('/')}>
        <div className="top-logo">H</div>
        <div>
          <div className="top-name">Харвестер<em>.</em></div>
          <div className="top-sub">Новости агрорынка</div>
        </div>
      </button>
      <nav className="top-nav">
        <button className="on" onClick={() => onNavigate('/')}>
          <FileText />
          Новости
        </button>
      </nav>
      <div className="top-r">
        <button className="top-ic" aria-label="Поиск" title="Поиск" onClick={onSearchClick}><Search /></button>
        <button className="top-ic" aria-label="Обновить" title="Обновить новости" onClick={onRefresh}><RefreshCw /></button>
      </div>
    </header>
  );
}
