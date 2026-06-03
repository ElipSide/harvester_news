/**
 * Strips Telegram-style markdown and decorative symbols from a news summary,
 * leaving clean plain text for display in compact news cards.
 *
 * Handles:
 *  - Markdown links     [label](url) → label
 *  - Bold/italic        *text*  _text_
 *  - Strikethrough      ~~text~~
 *  - Spoiler            ||text||
 *  - Inline code        `code` (removed entirely)
 *  - Decorative bullets ● • ► ➡ ♦ etc.
 *  - Unicode emoji      🌾 🚢 etc.
 *  - Newlines → space
 *  - Orphaned punctuation after symbol removal
 */
export function cleanSummary(text: string | null | undefined): string {
  if (!text) return '';

  let s = text
    // Markdown links [label](url) → keep label
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    // Telegram bold *text* — strip markers, keep content
    .replace(/\*([^*\r\n]+)\*/g, '$1')
    // Italic _text_
    .replace(/_([^_\r\n]+)_/g, '$1')
    // Strikethrough ~~text~~
    .replace(/~~([^~\r\n]+)~~/g, '$1')
    // Spoiler ||text||
    .replace(/\|\|([^|\r\n]+)\|\|/g, '$1')
    // Inline code — drop entirely (usually not prose)
    .replace(/`[^`\r\n]+`/g, '')
    // Decorative bullet/arrow symbols common in Telegram posts
    .replace(/[●•►▶▸▹▻→⇒➡➢➣➤➥➦♦♠♣♥◆◇■□▪▫◉○✦✧❖]/g, '')
    // Unicode emoji planes 1–15 (🌾 🚢 🔥 etc.)
    .replace(/[\u{1F000}-\u{1FFFF}]/gu, '')
    // Misc symbols and dingbats (U+2600–U+27BF): ☀ ✓ ➔ etc.
    .replace(/[\u{2600}-\u{27BF}]/gu, '')
    // Leftover bare asterisks (e.g. "* " used as a bullet prefix)
    .replace(/\*+/g, '')
    // Newlines → space
    .replace(/[\r\n]+/g, ' ')
    // " ." " ," etc. — orphaned punctuation after symbol removal
    .replace(/ +([.,;:!?])/g, '$1')
    // Collapse multiple spaces
    .replace(/ {2,}/g, ' ')
    .trim();

  return s;
}
