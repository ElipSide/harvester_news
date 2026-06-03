import { useState } from 'react';
import { FileText } from 'lucide-react';

export function NewsImage({ src, fallbackSrc, alt, className = '', fallbackClassName = '' }: {
  src?: string | null;
  fallbackSrc?: string | null;
  alt?: string;
  className?: string;
  fallbackClassName?: string;
}) {
  // Список источников в порядке приоритета, без пустых
  const sources = [src, fallbackSrc].filter(Boolean) as string[];
  const [idx, setIdx] = useState(0);

  const currentSrc = sources[idx] ?? null;

  if (!currentSrc) {
    return (
      <div className={`news-image-fallback ${fallbackClassName}`} aria-label="Фото отсутствует">
        <FileText />
        <span>Нет фото</span>
      </div>
    );
  }

  return (
    <img
      className={className}
      src={currentSrc}
      alt={alt || ''}
      loading="lazy"
      decoding="async"
      onError={() => setIdx(i => i + 1)}
    />
  );
}
