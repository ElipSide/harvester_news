import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { FullGraphResponse, FullGraphNode } from '../types';
import { cleanSummary } from '../utils/text';

// Полный порт explorer-а из примера harvester_tree.html: эго-граф вокруг фокус-события,
// X = время, Y = ярусы каналов (тема/игрок — центр, география — вверх, продукт — вниз),
// колесо недель, выпадашка сюжетов, фильтры каналов, навигация кликом по узлу.

type Ch = 'A' | 'P' | 'G' | 'T';
const CH: Record<Ch, { c: string; n: string; f: 'a' | 'p' | 'g' | 't' }> = {
  A: { c: '#6E5BD6', n: 'игрок', f: 'a' },
  P: { c: '#1B7A3E', n: 'продукт', f: 'p' },
  G: { c: '#D97706', n: 'регион', f: 'g' },
  T: { c: '#1E4FB0', n: 'тема', f: 't' },
};
const INK = '#15161A', GREY = '#9C9A92';
const MON = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const rOf = (sg: number) => 11 + (clamp(sg || 60, 60, 97) - 60) / 37 * 6.4;
const sigc = (s: number) => (s < 85 ? '#A1361B' : s < 92 ? '#1B7A3E' : '#125A2C');
const dayOf = (iso: string) => new Date(`${iso}T00:00:00`).getTime();
const weekOf = (iso: string) => Math.floor(dayOf(iso) / (7 * 86400000));
const dpart = (iso: string) => {
  const d = new Date(`${iso}T00:00:00`);
  return { d: String(d.getDate()).padStart(2, '0'), m: d.getMonth() };
};

// ── пути связей (roundedPath / diag45) ──
function roundedPath(pts: number[][], r: number): string {
  let d = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const px = pts[i - 1][0], py = pts[i - 1][1], cx = pts[i][0], cy = pts[i][1], nx = pts[i + 1][0], ny = pts[i + 1][1];
    let ix = cx - px, iy = cy - py; const li = Math.hypot(ix, iy) || 1; ix /= li; iy /= li;
    let ox = nx - cx, oy = ny - cy; const lo = Math.hypot(ox, oy) || 1; ox /= lo; oy /= lo;
    const rr = Math.min(r, li / 2, lo / 2);
    d += ` L${(cx - ix * rr).toFixed(1)} ${(cy - iy * rr).toFixed(1)} Q${cx.toFixed(1)} ${cy.toFixed(1)} ${(cx + ox * rr).toFixed(1)} ${(cy + oy * rr).toFixed(1)}`;
  }
  const L = pts[pts.length - 1];
  return d + ` L${L[0].toFixed(1)} ${L[1].toFixed(1)}`;
}
function diag45(px: number, py: number, cx: number, cy: number): string {
  const adx = Math.abs(cx - px), ady = Math.abs(cy - py), hd = Math.sign(cx - px) || 1, vd = Math.sign(cy - py) || 1;
  if (ady < 2) return roundedPath([[px, py], [cx, cy]], 13);
  if (adx >= ady) { const bx = px + hd * ady; return roundedPath([[px, py], [bx, cy], [cx, cy]], 13); }
  const by = py + vd * adx; return roundedPath([[px, py], [cx, by], [cx, cy]], 13);
}

type Branch = { id: number; ch: Ch; lab: string | null; story: boolean; sid?: number; role?: 'snext' | 'sprev'; x: number; y: number; lvl: number };
type TipState = { x: number; y: number; below: boolean; html: string } | null;

export function StoryTimeline({ graph, focusEventId, onOpenNews }: { graph: FullGraphResponse; focusEventId: number | null; onOpenNews: (id: number) => void }) {
  const [focus, setFocus] = useState<number | null>(focusEventId);
  const [active, setActive] = useState<number | null>(null);
  const [on, setOn] = useState<Record<Ch, boolean>>({ A: true, P: true, G: true, T: true });
  const [visited, setVisited] = useState<Set<number>>(new Set());
  const [tip, setTip] = useState<TipState>(null);
  const [width, setWidth] = useState(900);
  const [kpop, setKpop] = useState(false);
  const [kq, setKq] = useState('');
  const stageRef = useRef<HTMLDivElement>(null);
  const wheelRef = useRef<HTMLDivElement>(null);
  const wheelProg = useRef(false);

  // ── сброс фокуса при смене новости/графа ──
  useEffect(() => {
    setVisited(new Set());
    setKpop(false);
    setFocus(focusEventId);
    const fn = focusEventId != null ? graph.nodes.find((n) => n.id === focusEventId) : null;
    setActive(fn && fn.s.length ? fn.s[0] : null);
  }, [focusEventId, graph]);

  // ── производные индексы ──
  const idx = useMemo(() => {
    if (!graph) return null;
    const NODE: Record<number, FullGraphNode> = {};
    graph.nodes.forEach((n) => { NODE[n.id] = n; });
    const STORY: Record<number, { id: number; name: string; color: string; ev: number[] }> = {};
    graph.stories.forEach((s) => { STORY[s.id] = s; });
    const DAY: Record<number, number> = {};
    graph.nodes.forEach((n) => { DAY[n.id] = dayOf(n.date); });
    const NEI: Record<number, { to: number; w: number }[]> = {};
    graph.edges.forEach(([a, b, w]) => {
      (NEI[a] = NEI[a] || []).push({ to: b, w }); (NEI[b] = NEI[b] || []).push({ to: a, w });
    });
    const IDX: Record<'p' | 'g' | 't' | 'a', Record<string, number[]>> = { p: {}, g: {}, t: {}, a: {} };
    graph.nodes.forEach((n) => (['p', 'g', 't', 'a'] as const).forEach((f) => n[f].forEach((v) => {
      (IDX[f][v] = IDX[f][v] || []).push(n.id);
    })));
    const WEEKS = [...new Set(graph.nodes.map((n) => weekOf(n.date)))].sort((a, b) => a - b);
    const WKBEST: Record<number, number> = {};
    graph.nodes.forEach((n) => {
      const w = weekOf(n.date), b = WKBEST[w];
      const better = b == null || n.sg > NODE[b].sg || (n.sg === NODE[b].sg && n.s.length > NODE[b].s.length);
      if (better) WKBEST[w] = n.id;
    });
    return { NODE, STORY, DAY, NEI, IDX, WEEKS, WKBEST };
  }, [graph]);

  // ── измерение ширины сцены ──
  useLayoutEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const update = () => setWidth(Math.max(320, el.clientWidth));
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [graph]);

  const MOB = width <= 640;
  const caps = (): Record<Ch, number> => (MOB ? { A: 1, P: 3, G: 3, T: 3 } : { A: 2, P: 3, G: 3, T: 3 });

  // ── эго-граф: layout фокуса ──
  const lay = useMemo(() => {
    if (!idx || focus == null || !idx.NODE[focus]) return null;
    const { NODE, STORY, DAY, IDX } = idx;
    const fn = NODE[focus];
    const S = fn.s || [];
    const prim = active != null && S.includes(active) ? active : (S.length ? S[0] : null);
    const used = new Set<number>([focus]);
    const story: { id: number; role: 'snext' | 'sprev'; sid: number }[] = [];
    S.forEach((sid) => {
      const ev = STORY[sid]?.ev || []; const i = ev.indexOf(focus);
      if (i >= 0 && i + 1 < ev.length && !used.has(ev[i + 1])) { used.add(ev[i + 1]); story.push({ id: ev[i + 1], role: 'snext', sid }); }
    });
    if (prim != null) {
      const ev = STORY[prim]?.ev || []; const i = ev.indexOf(focus);
      if (i - 1 >= 0 && !used.has(ev[i - 1])) { used.add(ev[i - 1]); story.push({ id: ev[i - 1], role: 'sprev', sid: prim }); }
    }
    const c = caps(), fday = DAY[focus];
    const facet: { id: number; ch: Ch; lab: string }[] = [];
    (['A', 'P', 'G', 'T'] as Ch[]).forEach((ax) => {
      if (!on[ax]) return; const cap = c[ax]; if (cap <= 0) return; const f = CH[ax].f;
      const seen = new Set<number>(); const later: { id: number; lab: string; sg: number; dd: number }[] = []; const earlier: typeof later = [];
      (fn[f] || []).forEach((ent) => (IDX[f][ent] || []).forEach((id) => {
        if (id === focus || used.has(id) || seen.has(id)) return; seen.add(id);
        const dd = (DAY[id] - fday) / 864e5;
        (dd >= 0 ? later : earlier).push({ id, lab: ent, sg: NODE[id].sg, dd });
      }));
      later.sort((a, b) => (a.dd - b.dd) || (b.sg - a.sg));
      earlier.sort((a, b) => (b.dd - a.dd) || (b.sg - a.sg));
      let capL = Math.ceil(cap / 2), capE = cap - capL;
      if (later.length < capL) { capE += capL - later.length; capL = later.length; }
      if (earlier.length < capE) { capL = Math.min(later.length, capL + (capE - earlier.length)); capE = earlier.length; }
      later.slice(0, capL).concat(earlier.slice(0, capE)).forEach((p) => { used.add(p.id); facet.push({ id: p.id, ch: ax, lab: p.lab }); });
    });

    // канал story-ветки = первая общая грань focus↔node по приоритету A→P→G→T
    const sharedCh = (id: number): { ch: Ch; lab: string } => {
      for (const ax of ['A', 'P', 'G', 'T'] as Ch[]) {
        const f = CH[ax].f; const B = new Set(NODE[id][f]); const hit = (fn[f] || []).find((x) => B.has(x));
        if (hit) return { ch: ax, lab: hit };
      }
      return { ch: 'T', lab: 'связь' };
    };
    const branches: Branch[] = facet.map((b) => ({ id: b.id, ch: b.ch, lab: b.lab, story: false, x: 0, y: 0, lvl: 0 }))
      .concat(story.map((o) => { const c2 = sharedCh(o.id); return { id: o.id, ch: c2.ch, lab: c2.lab, story: true, sid: o.sid, role: o.role, x: 0, y: 0, lvl: 0 }; }));

    // X — время
    const W = width;
    const GAP = MOB ? 50 : 62, PAD = MOB ? 40 : 66, R = 16, mTop = MOB ? 36 : 40, mBot = MOB ? 36 : 40, MINDX = MOB ? 34 : 46;
    const order = [focus, ...branches.map((b) => b.id)].sort((a, b) => (DAY[a] - DAY[b]) || (a - b));
    const rank: Record<number, number> = {}; order.forEach((id, i) => { rank[id] = i; });
    const n = order.length, avail = W - 2 * PAD, stepX = n > 1 ? Math.min(104, avail / (n - 1)) : 0, totalW = (n - 1) * stepX, x0 = PAD + (avail - totalW) / 2;
    const X = (id: number) => x0 + rank[id] * stepX;
    const fx = X(focus);
    branches.forEach((b) => { b.x = X(b.id); });
    // Y — ярусы
    const occ: Record<number, number[]> = { 0: [fx] };
    const freeAt = (l: number, x: number) => { const a = occ[l]; return !a || a.every((xx) => Math.abs(xx - x) >= MINDX); };
    const spread = (arr: Branch[], pair: [number, number]) => arr.slice().sort((a, b) => a.x - b.x).forEach((nd, i) => {
      const ord = i % 2 ? [pair[1], pair[0]] : [pair[0], pair[1]];
      let lv = ord.find((l) => freeAt(l, nd.x));
      if (lv == null) { lv = pair[1]; const d = Math.sign(lv) || 1; while (!freeAt(lv, nd.x)) lv += d; }
      (occ[lv] = occ[lv] || []).push(nd.x); nd.lvl = lv;
    });
    const placeC = (arr: Branch[], cands: number[]) => arr.slice().sort((a, b) => a.x - b.x).forEach((nd) => {
      let lv = cands.find((l) => freeAt(l, nd.x)); if (lv == null) lv = cands[cands.length - 1];
      (occ[lv] = occ[lv] || []).push(nd.x); nd.lvl = lv;
    });
    placeC(branches.filter((b) => b.ch === 'T'), [0, 1, -1, 2, -2, 3, -3]);
    placeC(branches.filter((b) => b.ch === 'A'), [0, 1, -1, 2, -2, 3, -3]);
    spread(branches.filter((b) => b.ch === 'G'), [1, 2]);
    spread(branches.filter((b) => b.ch === 'P'), [-1, -2]);
    let upMax = 0, dnMax = 0;
    branches.forEach((b) => { if (b.lvl > upMax) upMax = b.lvl; if (-b.lvl > dnMax) dnMax = -b.lvl; });
    upMax = Math.max(1, upMax); dnMax = Math.max(1, dnMax);
    const cy = mTop + R + upMax * GAP, H = (upMax + dnMax) * GAP + 2 * R + mTop + mBot;
    branches.forEach((b) => { b.y = cy - b.lvl * GAP; });
    return { fn, fx, cy, branches, W, H, prim };
  }, [idx, focus, active, on, width, MOB]);

  // ── навигация ──
  const navTo = (id: number) => {
    if (!idx) return;
    setVisited((v) => new Set(v).add(focus!));
    setFocus(id);
    const S = idx.NODE[id]?.s || [];
    setActive((a) => (a != null && S.includes(a) ? a : (S.length ? S[0] : null)));
    setTip(null);
  };

  // ── колесо недель: центрируем текущую ──
  useEffect(() => {
    if (!idx || focus == null || !wheelRef.current) return;
    const w = weekOf(idx.NODE[focus].date);
    const i = idx.WEEKS.indexOf(w);
    const el = wheelRef.current.querySelector<HTMLElement>(`[data-wi="${i}"]`);
    if (el) {
      wheelProg.current = true;
      const t = el.offsetLeft + el.offsetWidth / 2 - wheelRef.current.clientWidth / 2;
      wheelRef.current.scrollTo({ left: Math.max(0, t), behavior: 'auto' });
      setTimeout(() => { wheelProg.current = false; }, 140);
    }
  }, [idx, focus, width]);

  if (focus == null || !idx || !lay || graph.nodes.length === 0) return null;

  const { NODE, STORY, WEEKS, WKBEST } = idx;
  const { fn, fx, cy, branches, W, H } = lay;
  const RS = MOB ? 0.74 : 1;
  const fr = Math.max(MOB ? 13 : 16, rOf(fn.sg) * RS + 2);
  const xs = [fx, ...branches.map((b) => b.x)];
  const mnx = Math.min(...xs), mxx = Math.max(...xs);

  const tipHtml = (node: FullGraphNode, b: Branch | null, kind: 'focus' | 'branch') => {
    let conn: string;
    if (kind === 'focus') conn = '<span>текущее</span>';
    else if (b?.story && b.role === 'snext') conn = `<span>↳ следующий в сюжете «${STORY[b.sid!]?.name || ''}»</span>`;
    else if (b?.story && b.role === 'sprev') conn = `<span>↰ предыдущий в сюжете «${STORY[b.sid!]?.name || ''}»</span>`;
    else conn = `<span>${b ? CH[b.ch].n : ''}: ${b?.lab || ''}</span>`;
    const vis = b && visited.has(b.id) && kind !== 'focus' ? ' · пройдено' : '';
    return `<div class="stl-tip-d"><span class="stl-tip-date">${(() => { const p = dpart(node.date); return `${p.d}.${String(p.m + 1).padStart(2, '0')}`; })()}</span><span class="stl-tip-sg">σ ${node.sg}</span><span class="stl-tip-conn">${conn}${vis}</span></div><div class="stl-tip-ti">${cleanSummary(node.ti)}</div>`;
  };

  const A = active;

  return (
    <section className="stl-wrap">
      {/* сцена графа */}
      <div className="stl-stage" ref={stageRef} style={{ height: H }} onPointerLeave={() => setTip(null)}>
        <svg className="stl-svg" viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none">
          <line x1={mnx - 14} y1={cy} x2={mxx + 14} y2={cy} stroke="#E1E0DA" strokeWidth={1} />
          {branches.map((b, i) => {
            const act = b.story && b.sid === A;
            const col = CH[b.ch].c, w = act ? 5.4 : (b.story ? 3.2 : 2.2), op = act ? 0.85 : (b.story ? 0.4 : 0.28);
            return <path key={`e${i}`} d={diag45(fx, cy, b.x, b.y)} fill="none" stroke={col} strokeWidth={w} strokeOpacity={op} strokeLinecap="round" strokeLinejoin="round" />;
          })}
          {/* узлы-ветки */}
          {branches.map((b) => {
            const col = CH[b.ch].c;
            const r = Math.round(rOf(NODE[b.id].sg) * RS);
            const isNext = b.story && b.role === 'snext';
            const isPrev = b.story && b.role === 'sprev';
            const dotCol = isNext ? col : GREY;
            const showDot = isNext || isPrev || visited.has(b.id);
            const bg = `color-mix(in srgb, ${col} 8%, #fff)`;
            return (
              <g key={b.id} className="stl-node" transform={`translate(${b.x},${b.y})`}
                onPointerEnter={() => setTip({ x: b.x, y: b.y, below: b.y < 96, html: tipHtml(NODE[b.id], b, 'branch') })}
                onPointerLeave={() => setTip(null)}
                onClick={() => { setTip(null); const mid = NODE[b.id].main_news_id; if (mid != null) onOpenNews(mid); else navTo(b.id); }}
                style={{ cursor: 'pointer' }}>
                <g className="stl-node-sc">
                  <circle r={r} fill={bg} stroke={col} strokeWidth={2.5} className="stl-node-base" />
                  {showDot && <circle className={isNext ? 'stl-core pulse' : ''} r={Math.max(3, Math.round(r * 0.46))} fill={dotCol} />}
                </g>
              </g>
            );
          })}
          {/* фокус */}
          <g className="stl-node" transform={`translate(${fx},${cy})`}
            onPointerEnter={() => setTip({ x: fx, y: cy, below: cy < 96, html: tipHtml(fn, null, 'focus') })}
            onPointerLeave={() => setTip(null)}
            onClick={() => fn.main_news_id && onOpenNews(fn.main_news_id)}
            style={{ cursor: fn.main_news_id ? 'pointer' : 'default' }}>
            <g className="stl-node-sc">
              <circle r={fr + 5} className="stl-halo" />
              <circle r={fr} fill="#fff" stroke={INK} strokeWidth={3} className="stl-node-base" />
              <circle r={Math.max(4.5, fr * 0.4)} fill={INK} />
            </g>
          </g>
        </svg>
        {tip && (
          <div className={`stl-tip${tip.below ? ' below' : ''} on`}
            style={{ left: `${(tip.x / W) * 100}%`, top: tip.below ? tip.y + 16 : tip.y - 16 }}
            dangerouslySetInnerHTML={{ __html: tip.html }} />
        )}
      </div>

      {/* ось времени */}
      <div className="stl-gcap"><span className="stl-axk">ось времени</span></div>

      {/* колесо недель */}
      <div className="stl-scrub">
        <div className="stl-wheelwrap">
          <div className="stl-wheel" ref={wheelRef}
            onScroll={() => {
              if (wheelProg.current || !wheelRef.current) return;
              const wr = wheelRef.current.getBoundingClientRect(), c = wr.left + wr.width / 2;
              let best = -1, bd = 1e9;
              wheelRef.current.querySelectorAll<HTMLElement>('.stl-wk').forEach((it) => {
                const r = it.getBoundingClientRect(), ic = r.left + r.width / 2, d = Math.abs(ic - c);
                if (d < bd) { bd = d; best = Number(it.dataset.wi); }
              });
              if (best >= 0) {
                const id = WKBEST[WEEKS[best]];
                if (id != null && id !== focus) {
                  clearTimeout((wheelRef.current as any)._t);
                  (wheelRef.current as any)._t = setTimeout(() => navTo(id), 120);
                }
              }
            }}>
            <span className="stl-wpad" />
            {WEEKS.map((w, i) => {
              const node = NODE[WKBEST[w]]; const p = dpart(node.date);
              const prevM = i > 0 ? dpart(NODE[WKBEST[WEEKS[i - 1]]].date).m : -1;
              const cur = weekOf(NODE[focus].date) === w;
              return (
                <button key={w} className={`stl-wk${cur ? ' on' : ''}`} data-wi={i} onClick={() => navTo(WKBEST[w])}>
                  <span className="stl-wmo">{p.m !== prevM ? MON[p.m] : ''}</span>
                  {p.d}.{String(p.m + 1).padStart(2, '0')}
                </button>
              );
            })}
            <span className="stl-wpad" />
          </div>
          <span className="stl-wcenter" />
        </div>
      </div>

      {/* легенда */}
      <div className="stl-legend">
        <span className="stl-lgrp stl-lgfilter">
          <span className="stl-lgcap">каналы</span>
          {(['P', 'G', 'T', 'A'] as Ch[]).map((ch) => (
            <button key={ch} className={`stl-lg2 chf${on[ch] ? '' : ' off'}`} onClick={() => setOn((s) => ({ ...s, [ch]: !s[ch] }))}>
              <i style={{ borderColor: CH[ch].c }} />{CH[ch].n}
            </button>
          ))}
        </span>
        <span className="stl-lgrp stl-lgsec">
          <span className="stl-lgcap">связь</span>
          <span className="stl-lg2"><span className="stl-lgl thin" />обычная</span>
          <span className="stl-lg2"><span className="stl-lgl" />сюжет</span>
        </span>
        <span className="stl-lgrp stl-lgsec">
          <span className="stl-lgcap">в сюжете</span>
          <span className="stl-lg2"><span className="stl-dotleg" style={{ ['--c' as any]: INK }} />текущее</span>
          <span className="stl-lg2"><span className="stl-dotleg" style={{ ['--c' as any]: GREY }} />предыдущий</span>
          <span className="stl-lg2"><span className="stl-dotleg pulse" style={{ ['--c' as any]: '#1E4FB0' }} />следующий</span>
        </span>
      </div>

      {/* выпадашка всех сюжетов */}
      {kpop && (
        <div className="stl-kpop" onClick={() => setKpop(false)}>
          <div className="stl-kpwrap" onClick={(e) => e.stopPropagation()}>
            <div className="stl-kphead"><span className="stl-kptitle">Сюжеты</span><button className="stl-kpx" onClick={() => setKpop(false)}>✕</button></div>
            <input className="stl-kps" placeholder="поиск сюжета" value={kq} onChange={(e) => setKq(e.target.value)} autoFocus />
            <div className="stl-kpl">
              {[...graph.stories].sort((a, b) => b.ev.length - a.ev.length)
                .filter((s) => !kq.trim() || s.name.toLowerCase().includes(kq.toLowerCase().trim()))
                .map((s) => (
                  <div key={s.id} className={`stl-kpi${s.id === active ? ' on' : ''}`}
                    onClick={() => { const mid = s.ev[Math.floor(s.ev.length / 2)]; setKpop(false); setActive(s.id); if (mid != null) navTo(mid); }}>
                    <span className="stl-kpi-nm">{s.name}</span><span className="stl-kpi-n">{s.ev.length}</span>
                  </div>
                ))}
              {![...graph.stories].some((s) => !kq.trim() || s.name.toLowerCase().includes(kq.toLowerCase().trim())) && <div className="stl-kpe">ничего не нашлось</div>}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
