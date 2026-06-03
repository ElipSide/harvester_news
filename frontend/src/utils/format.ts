export const MONTH_SHORT = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

export function formatDate(value: string | null): string {
  if (!value) return 'без даты';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return 'без даты';

  const today = new Date();
  const startToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const startDate = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.floor((startToday - startDate) / 86_400_000);

  if (days === 0) return 'сегодня';
  if (days === 1) return 'вчера';
  return `${d.getDate()} ${MONTH_SHORT[d.getMonth()]}`;
}

export function formatFullDate(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return `${d.getDate()} ${MONTH_SHORT[d.getMonth()]}`;
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat('ru-RU').format(value || 0);
}

export function pluralize(n: number, forms: [string, string, string]): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return forms[0];
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1];
  return forms[2];
}

export function cleanTopic(topic: string | undefined): string {
  return (topic || 'прочее').trim().toLowerCase();
}
