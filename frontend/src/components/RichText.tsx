import type { ReactNode } from 'react';

type Token =
  | { type: 'text'; value: string }
  | { type: 'bold'; value: string }
  | { type: 'link'; label: string; href: string };

function normalizeHref(href: string): string | null {
  const clean = href.trim();
  if (!clean) return null;
  if (/^https?:\/\//i.test(clean)) return clean;
  return null;
}

/** Strip decorative symbols that carry no meaning in prose */
function stripDecorative(text: string): string {
  return text
    // Symbol bullets and arrows used as decoration in Telegram posts
    .replace(/[●•◉○✦✧❖♦♠♣♥◆◇■□▪▫►▶▸▹▻→⇒➡➢➣➤➥➦]/g, '')
    // Arrow symbols (e.g. ↗ used after links)
    .replace(/[←-⇿]/gu, '')
    // Unicode emoji
    .replace(/[\u{1F000}-\u{1FFFF}]/gu, '')
    // Misc symbols & dingbats
    .replace(/[\u{2600}-\u{27BF}]/gu, '')
    // Collapse extra spaces after removal
    .replace(/ {2,}/g, ' ');
}

/**
 * Tokenize a single line of inline text.
 * Handles:
 *   [label](url)  → link
 *   *text*        → bold (Telegram markdown)
 *   bare URL      → link
 *   everything else → text (decorative symbols stripped)
 */
function tokenizeInline(raw: string): Token[] {
  const text = stripDecorative(raw);
  const result: Token[] = [];

  // Order matters: links first, then bold, then bare URLs
  const pattern = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|\*([^*\r\n]+)\*|(https?:\/\/[^\s<]+[^\s<.,;:!?])/gi;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text))) {
    if (match.index > lastIndex) {
      result.push({ type: 'text', value: text.slice(lastIndex, match.index) });
    }

    if (match[1] != null && match[2] != null) {
      // Markdown link
      const href = normalizeHref(match[2]);
      if (href) result.push({ type: 'link', label: match[1], href });
      else result.push({ type: 'text', value: match[1] });
    } else if (match[3] != null) {
      // Telegram bold *text*
      result.push({ type: 'bold', value: match[3] });
    } else if (match[4] != null) {
      // Bare URL
      const href = normalizeHref(match[4]);
      if (href) result.push({ type: 'link', label: match[4], href });
    }

    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    result.push({ type: 'text', value: text.slice(lastIndex) });
  }

  return result;
}

function renderInline(text: string): ReactNode[] {
  return tokenizeInline(text).map((token, index) => {
    if (token.type === 'bold') return <strong key={index}>{token.value}</strong>;
    if (token.type === 'link') {
      return (
        <a key={index} className="rich-link" href={token.href} target="_blank" rel="noreferrer"
          onClick={(e) => e.stopPropagation()}>
          {token.label}
        </a>
      );
    }
    return <span key={index}>{token.value}</span>;
  });
}

/** Lines starting with emoji/arrow bullets (space after symbol optional) */
function isBulletLine(line: string): boolean {
  // Standard markdown: "- item" or "1. item"
  if (/^\s*(?:-|\d+[.)])\s+/.test(line)) return true;
  // Telegram-style symbol bullets: ●, ➡, ► etc. (space optional)
  if (/^\s*[•●►▶▸▹→⇒➡➢➤]\s*\S/.test(line)) return true;
  return false;
}

function cleanBullet(line: string): string {
  return line
    .replace(/^\s*(?:-|\d+[.)])\s+/, '')
    .replace(/^\s*[•●►▶▸▹→⇒➡➢➤]\s*/, '')
    .trim();
}

/** ™ attribution lines at end of Telegram posts ("™ Агрономика") */
function isAttributionLine(line: string): boolean {
  return /^™\s/.test(line.trim());
}

export function RichText({ text, fallback }: { text: string | null | undefined; fallback?: string | null }) {
  const source = (text || fallback || '').trim();
  if (!source) return null;

  const blocks = source
    .replace(/\r\n/g, '\n')
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean);

  return (
    <div className="rich-text">
      {blocks.map((block, blockIndex) => {
        const lines = block
          .split('\n')
          .map((l) => l.trim())
          .filter((l) => l && !isAttributionLine(l));

        if (!lines.length) return null;

        if (lines.every(isBulletLine)) {
          return (
            <ul key={blockIndex} className="rich-list">
              {lines.map((line, i) => (
                <li key={i}>{renderInline(cleanBullet(line))}</li>
              ))}
            </ul>
          );
        }

        return (
          <p key={blockIndex}>
            {lines.map((line, i) => (
              <span key={i}>
                {renderInline(line)}
                {i < lines.length - 1 && <br />}
              </span>
            ))}
          </p>
        );
      })}
    </div>
  );
}
