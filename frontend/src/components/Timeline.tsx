import { useMemo, useRef, useState } from 'react';
import type { FacetItem, TimelineDay, TimelineResponse } from '../types';
import { formatNumber, pluralize } from '../utils/format';
import { topicColor } from '../utils/topic';

type Scale = 'day' | 'week' | 'month';

type TimelineBucket = {
  key: string;
  dateFrom: string;
  dateTo: string;
  total: number;
  topics: Record<string, number>;
  related: FacetItem[];
  daysCount: number;
  isCurrent: boolean;
  sourceDays: TimelineDay[];
};

const OTHER_TOPIC = 'остальное';
const LEG_LIMIT = 6;

const scaleLabels: Record<Scale, string> = { day: 'день', week: 'нед', month: 'мес' };
const bucketSizeByScale: Record<Scale, number> = { day: 1, week: 7, month: 30 };

function parseDate(v: string): Date { return new Date(`${v}T00:00:00`); }

function rangeLabel(dateFrom: string, dateTo: string): string {
  const from = parseDate(dateFrom);
  const to   = parseDate(dateTo);
  const mo = ['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек'];
  if (dateFrom === dateTo) return `${from.getDate()} ${mo[from.getMonth()]}`;
  if (from.getMonth() === to.getMonth()) return `${from.getDate()}–${to.getDate()} ${mo[to.getMonth()]}`;
  return `${from.getDate()} ${mo[from.getMonth()]} – ${to.getDate()} ${mo[to.getMonth()]}`;
}

function monthLabel(d: string): string {
  return ['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек'][parseDate(d).getMonth()];
}

function sortEntries(e: [string, number][]): [string, number][] {
  return e.filter(([n, c]) => Boolean(n) && c > 0).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'ru'));
}

function mergeTopics(days: TimelineDay[]): Record<string, number> {
  const m = new Map<string, number>();
  days.forEach(d => Object.entries(d.topics).forEach(([t, c]) => m.set(t, (m.get(t) || 0) + c)));
  return Object.fromEntries(sortEntries([...m.entries()]));
}

function collapseTopics(topics: Record<string, number>, topNames: string[], limit: number): [string, number][] {
  const raw = sortEntries(Object.entries(topics));
  const baseOther = raw.filter(([n]) => n === OTHER_TOPIC).reduce((s, [, c]) => s + c, 0);
  const entries = raw.filter(([n]) => n !== OTHER_TOPIC);
  if (entries.length <= limit) return baseOther > 0 ? [...entries, [OTHER_TOPIC, baseOther]] : entries;
  const topSet = new Set(topNames.slice(0, limit).filter(n => n !== OTHER_TOPIC));
  const visible: [string, number][] = [];
  let other = baseOther;
  entries.forEach(([n, c]) => topSet.has(n) ? visible.push([n, c]) : (other += c));
  visible.sort((a, b) => {
    const ai = topNames.indexOf(a[0]), bi = topNames.indexOf(b[0]);
    return ai !== bi ? ai - bi : b[1] - a[1];
  });
  if (other > 0) visible.push([OTHER_TOPIC, other]);
  return visible;
}

function segColor(topic: string, idx: number, topNames: string[]): string {
  if (topic === OTHER_TOPIC) return '#A7A49C';
  const gi = topNames.indexOf(topic);
  return topicColor(topic, gi >= 0 ? gi : idx);
}

function buildBuckets(items: TimelineDay[], scale: Scale): TimelineBucket[] {
  const size = bucketSizeByScale[scale];
  if (!items.length) return [];
  const newest = [...items].sort((a, b) => b.date.localeCompare(a.date));
  const buckets: TimelineBucket[] = [];
  for (let i = 0; i < newest.length; i += size) {
    const chunk = newest.slice(i, i + size);
    const chr = [...chunk].sort((a, b) => a.date.localeCompare(b.date));
    const dateFrom = chr[0].date, dateTo = chr[chr.length - 1].date;
    const total = chunk.reduce((s, d) => s + d.total, 0);
    const map = new Map<string, number>();
    chunk.forEach(d => { d.related.forEach(r => map.set(r.name, (map.get(r.name) || 0) + r.count)); });
    buckets.push({
      key: `${scale}:${dateFrom}:${dateTo}`, dateFrom, dateTo, total,
      topics: mergeTopics(chunk),
      related: sortEntries([...map.entries()]).slice(0, 8).map(([name, count]) => ({ name, count })),
      daysCount: chunk.length,
      isCurrent: i === 0,
      sourceDays: chr,
    });
  }
  return buckets;
}

function axisLabel(bucket: TimelineBucket, scale: Scale, idx: number): string | null {
  if (scale === 'month') return monthLabel(bucket.dateTo);
  if (scale === 'week') {
    if (idx === 0) return 'сегодня';
    if (idx % 4 !== 0) return null;
    return rangeLabel(bucket.dateFrom, bucket.dateFrom);
  }
  const d = parseDate(bucket.dateTo);
  if (idx === 0) return 'сегодня';
  if (d.getDate() === 1) return `${d.getDate()} ${monthLabel(bucket.dateTo)}`;
  if (d.getDay() === 1 && idx % 7 === 0) return String(d.getDate());
  return null;
}

export function Timeline({
  timeline,
  selectedDateFrom,
  selectedDateTo,
  onSelectRange,
  onSearchTopic,
}: {
  timeline: TimelineResponse | null;
  selectedDateFrom: string | null;
  selectedDateTo: string | null;
  onSelectRange: (dateFrom: string, dateTo: string, options?: { scroll?: boolean }) => void;
  onSearchTopic: (topic: string) => void;
}) {
  const [scale, setScale] = useState<Scale>('week');
  const [hovered, setHovered] = useState<{ bucket: TimelineBucket; idx: number } | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);

  const buckets = useMemo(() => buildBuckets(timeline?.items || [], scale), [timeline, scale]);
  const maxTotal = useMemo(() => Math.max(1, ...buckets.map(b => b.total)), [buckets]);
  const hasSelection = Boolean(selectedDateFrom && selectedDateTo);

  const topNames = useMemo(
    () => (timeline?.topics || []).filter(t => t.name !== OTHER_TOPIC).slice(0, LEG_LIMIT).map(t => t.name),
    [timeline],
  );

  // Legend: top topics over visible period
  const allTopics = useMemo(() => mergeTopics(timeline?.items || []), [timeline]);
  const legendEntries = useMemo(() => collapseTopics(allTopics, topNames, LEG_LIMIT), [allTopics, topNames]);
  const hiddenTopicCount = Math.max(0, (timeline?.topics || []).filter(t => t.name !== OTHER_TOPIC).length - LEG_LIMIT);

  // Active bucket indexes
  const activeBuckets = useMemo(() => {
    if (!hasSelection) return new Set<number>();
    const s = new Set<number>();
    buckets.forEach((b, i) => {
      if (b.dateTo >= selectedDateFrom! && b.dateFrom <= selectedDateTo!) s.add(i);
    });
    return s;
  }, [buckets, hasSelection, selectedDateFrom, selectedDateTo]);

  // Band overlay style (% positions)
  const bandStyle = useMemo(() => {
    if (!hasSelection || !buckets.length) return null;
    const sorted = [...activeBuckets].sort((a, b) => a - b);
    if (!sorted.length) return null;
    const n = buckets.length;
    const from = sorted[0], to = sorted[sorted.length - 1];
    return { left: `${(from / n) * 100}%`, width: `${((to - from + 1) / n) * 100}%` };
  }, [activeBuckets, buckets.length, hasSelection]);

  // Gridlines at 50% and 100%
  const glLines = useMemo(() => {
    if (!maxTotal || maxTotal <= 1) return [];
    const half = Math.round(maxTotal / 2);
    return [
      { pct: 50, label: String(half) },
      { pct: 100, label: String(maxTotal) },
    ];
  }, [maxTotal]);

  // Tooltip entries for hovered bucket
  const tipEntries = useMemo(() => {
    if (!hovered) return [];
    return collapseTopics(hovered.bucket.topics, topNames, 5);
  }, [hovered, topNames]);

  // Tooltip position: above hovered column, centered
  const tipStyle = useMemo(() => {
    if (!hovered || !buckets.length) return {};
    const n = buckets.length;
    const pct = ((hovered.idx + 0.5) / n) * 100;
    if (pct < 60) return { left: `${pct}%`, transform: 'translateX(-15%)', bottom: '100%', marginBottom: '6px' };
    return { right: `${100 - pct}%`, transform: 'translateX(15%)', bottom: '100%', marginBottom: '6px' };
  }, [hovered, buckets.length]);

  const clearRange = () => {
    if (timeline) onSelectRange(timeline.date_from, timeline.date_to, { scroll: false });
  };

  if (!timeline) {
    return <section className="tl2 tl2-skeleton">Загрузка активности…</section>;
  }

  return (
    <section className="tl2">
      {/* ── Header ── */}
      <div className="tl2-hd">
        <div className="tl2-hd-l">
          <span className="tl2-title">Активность рынка</span>
          <span className="tl2-meta">
            <span className="num">{formatNumber(timeline.total)}</span> новостей
            <span className="tl2-dot" />
            в среднем <span className="num">{timeline.avg_per_day}</span> в день
            {hasSelection && (
              <>
                <span className="tl2-dot" />
                <span className="tl2-rng">{rangeLabel(selectedDateFrom!, selectedDateTo!)}</span>
                <button className="tl2-clr" onClick={clearRange}>× сбросить</button>
              </>
            )}
          </span>
        </div>
        <div className="tl2-segs">
          {(['week', 'month', 'day'] as Scale[]).map(s => (
            <button key={s} className={`tl2-seg${scale === s ? ' on' : ''}`} onClick={() => setScale(s)}>
              {scaleLabels[s]}
            </button>
          ))}
        </div>
      </div>

      {/* ── Legend ── */}
      {legendEntries.length > 0 && (
        <div className="tl2-leg">
          {legendEntries.filter(([n]) => n !== OTHER_TOPIC).map(([topic, count], i) => (
            <button key={topic} className="tl2-leg-item" onClick={() => onSearchTopic(topic)}>
              <span className="tl2-leg-dot" style={{ background: segColor(topic, i, topNames) }} />
              {topic}
              <span className="tl2-leg-n">{count}</span>
            </button>
          ))}
          {hiddenTopicCount > 0 && (
            <span className="tl2-leg-more">+{hiddenTopicCount} тем</span>
          )}
        </div>
      )}

      {/* ── Chart ── */}
      <div className="tl2-chart" ref={chartRef} onMouseLeave={() => setHovered(null)}>
        {/* Columns + band + gridlines */}
        <div className="tl2-cols">
          {/* Gridlines */}
          {glLines.map(gl => (
            <div key={gl.pct} className="tl2-gl" style={{ bottom: `${gl.pct}%` }}>
              <span>{gl.label}</span>
            </div>
          ))}

          {buckets.map((bucket, idx) => {
            const isOut = hasSelection && !activeBuckets.has(idx);
            const hPct = bucket.total === 0 ? 2 : Math.max(3, (bucket.total / maxTotal) * 100);
            const entries = collapseTopics(bucket.topics, topNames, LEG_LIMIT);
            const segBase = Math.max(1, entries.reduce((s, [, c]) => s + c, 0));
            return (
              <div
                key={bucket.key}
                className={`tl2-col${isOut ? ' out' : ''}`}
                onMouseEnter={() => setHovered({ bucket, idx })}
                onClick={() => onSelectRange(bucket.dateFrom, bucket.dateTo, { scroll: false })}
                title={`${rangeLabel(bucket.dateFrom, bucket.dateTo)} · ${pluralize(bucket.total, ['новость', 'новости', 'новостей'])}`}
              >
                <div className="tl2-bar" style={{ height: `${hPct}%` }}>
                  {entries.map(([topic, count], ti) => (
                    <div
                      key={topic}
                      className="tl2-bseg"
                      style={{
                        height: `${Math.max(2, (count / segBase) * 100)}%`,
                        background: segColor(topic, ti, topNames),
                      }}
                    />
                  ))}
                </div>
              </div>
            );
          })}

          {/* Selection band */}
          {hasSelection && bandStyle && (
            <div className="tl2-band" style={bandStyle} />
          )}
        </div>

        {/* X-axis */}
        <div className="tl2-ax">
          {buckets.map((bucket, idx) => {
            const lbl = axisLabel(bucket, scale, idx);
            return (
              <div key={`ax-${bucket.key}`} className={`tl2-ax-col${bucket.isCurrent ? ' today' : ''}`}>
                {lbl && <span className="tl2-ax-lbl">{lbl}</span>}
              </div>
            );
          })}
        </div>

        {/* Tooltip */}
        {hovered && (
          <div className="tl2-tip" style={tipStyle}>
            <div className="tl2-tip-d">{rangeLabel(hovered.bucket.dateFrom, hovered.bucket.dateTo)}</div>
            {tipEntries.filter(([n]) => n !== OTHER_TOPIC).map(([topic, count], ti) => (
              <div key={topic} className="tl2-tip-row">
                <span className="tl2-tip-dot" style={{ background: segColor(topic, ti, topNames) }} />
                <span className="tl2-tip-lbl">{topic}</span>
                <span className="tl2-tip-v">{count}</span>
              </div>
            ))}
            <div className="tl2-tip-tot">
              <span className="tl2-tip-lbl">Итого</span>
              <span className="tl2-tip-v">{hovered.bucket.total}</span>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
