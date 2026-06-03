export const TOPIC_COLORS = ['var(--tc-reg)', 'var(--tc-price)', 'var(--tc-exp)', 'var(--tc-weath)', 'var(--tc-deal)', 'var(--ink-3)'];

const colorMap: Record<string, string> = {
  'регулятор': 'var(--tc-reg)',
  'цены': 'var(--tc-price)',
  'экспорт': 'var(--tc-exp)',
  'погода': 'var(--tc-weath)',
  'сделки': 'var(--tc-deal)',
};

export function topicColor(topic: string, idx = 0): string {
  return colorMap[topic.toLowerCase()] || TOPIC_COLORS[idx % TOPIC_COLORS.length];
}

export function topicClass(topic: string): string {
  const t = topic.toLowerCase();
  if (t.includes('погод')) return 'w';
  if (t.includes('экспорт')) return 'e';
  if (t.includes('сдел')) return 'd';
  return '';
}
