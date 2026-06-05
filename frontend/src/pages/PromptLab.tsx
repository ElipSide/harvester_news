import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { labApi, type LabEvent, type LabPreview, type LabSource } from '../api/client';

const box: CSSProperties = { maxWidth: 1100, margin: '0 auto', padding: '24px 20px 80px', fontFamily: 'Arial, sans-serif', color: '#15161A' };
const card: CSSProperties = { border: '1px solid #e3e6e3', borderRadius: 12, padding: 16, marginBottom: 16, background: '#fff' };
const label: CSSProperties = { display: 'block', fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.3, color: '#5a615a', marginBottom: 6 };
const taStyle: CSSProperties = { width: '100%', boxSizing: 'border-box', fontFamily: 'inherit', fontSize: 13.5, lineHeight: 1.5, padding: 10, border: '1px solid #cfd4cf', borderRadius: 8, resize: 'vertical' };

export default function PromptLab() {
  const [system, setSystem] = useState('');
  const [maxChars, setMaxChars] = useState<number>(2500);
  const [autoChars, setAutoChars] = useState<boolean>(true);
  const [active, setActive] = useState<boolean>(true);
  const [model, setModel] = useState('');

  const [q, setQ] = useState('');
  const [events, setEvents] = useState<LabEvent[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const [sources, setSources] = useState<LabSource[]>([]);
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const [sourcesError, setSourcesError] = useState<string | null>(null);

  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<LabPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);
  const pollRef = useRef<number | null>(null);

  const stopTimers = () => {
    if (timerRef.current) { window.clearInterval(timerRef.current); timerRef.current = null; }
    if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
  };

  useEffect(() => () => stopTimers(), []);

  useEffect(() => {
    labApi.defaults().then((d) => {
      setSystem(d.system_prompt || '');
      setMaxChars(d.max_source_chars || 2500);
      setActive(!!d.active);
      setModel(d.model || '');
    }).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    const t = window.setTimeout(() => {
      labApi.events(q, 100).then((r) => setEvents(r.items || [])).catch(() => {});
    }, 250);
    return () => window.clearTimeout(t);
  }, [q]);

  // Источники выбранного события (обрезка зависит от maxChars → дебаунс на смену лимита).
  useEffect(() => {
    if (selectedId == null) { setSources([]); setSourcesError(null); return; }
    setSourcesLoading(true); setSourcesError(null);
    const t = window.setTimeout(() => {
      labApi.sources(selectedId, maxChars)
        .then((r) => setSources(r.sources || []))
        .catch((e) => { setSources([]); setSourcesError(String(e)); })
        .finally(() => setSourcesLoading(false));
    }, 300);
    return () => window.clearTimeout(t);
  }, [selectedId, maxChars]);

  // Авто-расчёт «символов на источник»: общий бюджет ввода делим на число источников,
  // с поправкой на длину промта и обрамление, и не превышаем реальную длину источников.
  const computeAutoChars = (srcs: LabSource[], promptText: string): number => {
    const n = Math.max(1, srcs.length);
    const TOTAL = 16000;                              // целевой суммарный объём всех источников, симв.
    const overhead = promptText.length + n * 160;     // промт + шапки «### Источник N …»
    const budget = Math.max(3000, TOTAL - overhead);
    const longest = srcs.reduce((m, s) => Math.max(m, s.full_chars || 0), 0);
    let per = Math.floor(budget / n);
    per = Math.min(per, Math.max(longest, 600));       // нет смысла больше самого длинного источника
    per = Math.min(4000, Math.max(600, per));          // разумные рамки
    return Math.round(per / 100) * 100;
  };

  // В авто-режиме пересчитываем при смене источников/промта. full_chars не зависит от
  // обрезки, поэтому второй проход даёт то же значение — цикла нет.
  useEffect(() => {
    if (!autoChars || sources.length === 0) return;
    const v = computeAutoChars(sources, system);
    setMaxChars((prev) => (prev === v ? prev : v));
  }, [autoChars, sources, system]);

  const selected = useMemo(() => events.find((e) => e.id === selectedId) || null, [events, selectedId]);

  const errMsg = (r: LabPreview): string => {
    if (r.error === 'model_empty') return `Модель вернула пустой ответ ${r.attempts ?? ''} раз подряд — это поведение reasoning-модели, не промта. Нажми «Сгенерировать» ещё раз.`;
    if (r.error === 'unparsable') return 'Модель ответила, но не по формату (текст в «сыром ответе» ниже). Попробуй ещё раз.';
    if (r.error === 'timeout') return 'Превышено время ожидания. Нажми ещё раз.';
    if (r.error === 'ragflow_inactive') return 'RAGFlow выключен в настройках.';
    if (r.error === 'no_sources') return 'У события нет источников.';
    return r.error ? `Ошибка: ${r.error}` : 'Модель вернула пустой ответ';
  };

  const finish = (r: LabPreview | null, err: string | null) => {
    stopTimers();
    setLoading(false);
    if (r) setResult(r);
    if (err) setError(err);
  };

  const run = async () => {
    if (selectedId == null) { setError('Выберите событие'); return; }
    setError(null); setResult(null); setLoading(true); setElapsed(0);
    const t0 = Date.now();
    stopTimers();
    timerRef.current = window.setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 500);
    try {
      const started = await labApi.previewStart({ event_id: selectedId, system_prompt: system, max_source_chars: maxChars });
      if (!started.ok || !started.job_id) {
        finish({ ok: false, error: started.error }, started.error === 'no_sources' ? 'У события нет источников' : 'Не удалось запустить генерацию');
        return;
      }
      const jobId = started.job_id;
      // Поллим результат короткими запросами — ни один nginx не держит долгое соединение.
      pollRef.current = window.setInterval(async () => {
        try {
          const res = await labApi.previewResult(jobId);
          if (res.status === 'running') return;
          if (res.status === 'unknown') {
            finish(null, 'Задача потеряна сервером (перезапуск backend?). Запусти заново.');
            return;
          }
          const r = res.result || { ok: false };
          finish(r, r.ok ? null : errMsg(r));
        } catch (e) {
          // Разовый сетевой сбой при поллинге — не валим, ждём следующий тик.
          console.warn('poll error', e);
        }
      }, 3000);
    } catch (e) {
      finish(null, String(e));
    }
  };

  return (
    <div style={box}>
      <h1 style={{ fontSize: 22, margin: '0 0 4px' }}>Лаборатория промтов</h1>
      <p style={{ margin: '0 0 16px', color: '#5a615a', fontSize: 13 }}>
        Тест генерации статьи события. Результат показывается только здесь — <b>в БД ничего не пишется</b>.
        {' '}RAGFlow: {active ? <span style={{ color: '#1d7a33' }}>активен</span> : <span style={{ color: '#b00' }}>выключен</span>} · модель: {model}
      </p>

      <div style={card}>
        <label style={label}>Событие</label>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Поиск по заголовку…"
          style={{ ...taStyle, marginBottom: 8 }}
        />
        <select
          value={selectedId ?? ''}
          onChange={(e) => setSelectedId(e.target.value ? Number(e.target.value) : null)}
          size={8}
          style={{ width: '100%', boxSizing: 'border-box', fontFamily: 'inherit', fontSize: 13, padding: 6, border: '1px solid #cfd4cf', borderRadius: 8 }}
        >
          {events.map((e) => (
            <option key={e.id} value={e.id}>
              #{e.id} · {e.date_to ? e.date_to.slice(0, 10) : '—'} · {e.sources} ист. · {e.title}
            </option>
          ))}
        </select>
        {selected && <div style={{ marginTop: 8, fontSize: 13, color: '#15161A' }}>Выбрано: <b>#{selected.id}</b> — {selected.title}</div>}
      </div>

      {selectedId != null && (
        <div style={card}>
          <label style={label}>
            Оригинальные тексты источников {sources.length > 0 && <span style={{ color: '#5a615a', fontWeight: 400 }}>({sources.length}) — ровно то, что видит модель</span>}
          </label>
          {sourcesLoading && <div style={{ fontSize: 13, color: '#5a615a' }}>Загрузка источников…</div>}
          {sourcesError && <div style={{ fontSize: 13, color: '#b00' }}>{sourcesError}</div>}
          {!sourcesLoading && !sourcesError && sources.length === 0 && (
            <div style={{ fontSize: 13, color: '#5a615a' }}>У события нет источников.</div>
          )}
          <div style={{ display: 'grid', gap: 12, marginTop: sources.length ? 4 : 0 }}>
            {sources.map((s) => (
              <div key={s.id ?? s.index} style={{ border: '1px solid #e3e6e3', borderRadius: 8, padding: 12, background: '#fafbfa' }}>
                <div style={{ fontSize: 12, color: '#5a615a', marginBottom: 4 }}>
                  <b>#{s.index}</b>
                  {s.source && <> · {s.source}</>}
                  {s.date && <> · {s.date}</>}
                  {s.id != null && <> · id {s.id}</>}
                  {' · '}{s.shown_chars} симв.{s.truncated && <span style={{ color: '#b06a00' }}> (обрезано из {s.full_chars})</span>}
                </div>
                {s.title && <div style={{ fontSize: 14.5, fontWeight: 700, marginBottom: 4 }}>{s.title}</div>}
                {s.tags.length > 0 && <div style={{ fontSize: 12, color: '#5a615a', marginBottom: 6 }}>Теги: {s.tags.join(', ')}</div>}
                <div style={{ fontSize: 13.5, lineHeight: 1.55, whiteSpace: 'pre-wrap', color: '#15161A' }}>{s.text || <span style={{ color: '#9aa39a' }}>— пустой текст —</span>}</div>
                {s.link && <div style={{ marginTop: 6 }}><a href={s.link} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, color: '#1d7a33' }}>Открыть источник ↗</a></div>}
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={card}>
        <label style={label}>Системный промт</label>
        <textarea value={system} onChange={(e) => setSystem(e.target.value)} rows={16} style={ taStyle } />
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-end', marginTop: 12, flexWrap: 'wrap' }}>
          <div>
            <label style={label}>Символов на источник</label>
            <input type="number" value={maxChars} min={300} max={8000} step={100} disabled={autoChars}
              onChange={(e) => setMaxChars(Number(e.target.value) || 2500)}
              style={{ width: 120, padding: 8, border: '1px solid #cfd4cf', borderRadius: 8, fontFamily: 'inherit', background: autoChars ? '#f2f4f2' : '#fff', color: autoChars ? '#5a615a' : '#15161A' }} />
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer', paddingBottom: 8 }}>
            <input type="checkbox" checked={autoChars} onChange={(e) => setAutoChars(e.target.checked)} />
            авто
          </label>
          {autoChars && (
            <div style={{ fontSize: 12, color: '#5a615a', paddingBottom: 8 }}>
              рассчитано по длине источников ({sources.length || '—'}) и промта; бюджет ≈16k симв.
            </div>
          )}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16 }}>
        <button onClick={run} disabled={loading || selectedId == null}
          style={{ padding: '10px 20px', fontSize: 14, fontWeight: 700, color: '#fff', background: loading ? '#9aa39a' : '#1d7a33', border: 0, borderRadius: 8, cursor: loading ? 'default' : 'pointer' }}>
          {loading ? `Генерация… ${elapsed}s` : 'Сгенерировать'}
        </button>
        {error && <span style={{ color: '#b00', fontSize: 13 }}>{error}</span>}
      </div>

      {loading && (
        <div style={card} aria-busy="true">
          <div style={{ fontSize: 13, color: '#5a615a', marginBottom: 12 }}>
            Генерация статьи… {elapsed}s · обычно 2–4 мин (модель «думает» ~3 мин на заход; при пустом ответе идёт повтор, до ~10 мин). Не нажимай повторно и не закрывай страницу.
          </div>
          <div className="ev2-body-skeleton">
            <span className="sk-line" /><span className="sk-line" /><span className="sk-line" />
            <span className="sk-line sk-short" /><span className="sk-line" /><span className="sk-line" />
            <span className="sk-line" /><span className="sk-line sk-short" />
          </div>
        </div>
      )}

      {!loading && result && result.ok && (
        <div style={card}>
          <div style={{ fontSize: 12, color: '#5a615a', marginBottom: 8 }}>
            слов: <b>{result.words}</b> · источников: {result.sources_used} · попыток: {result.attempts ?? '—'} · модель: {result.model}
          </div>
          {result.used_fallback && (
            <div style={{ fontSize: 12, color: '#b06a00', marginBottom: 8 }}>
              ⚠ Получено plain-fallback'ом (последняя попытка, без JSON): первые попытки с твоим промтом дали пустой ответ. Стиль/правила твои, форма вывода упрощена.
            </div>
          )}
          <h2 style={{ fontSize: 20, margin: '0 0 12px' }}>{result.title}</h2>
          <div style={{ fontSize: 15, lineHeight: 1.62, whiteSpace: 'pre-wrap' }}>{result.article}</div>
        </div>
      )}

      {result && (
        <details style={card}>
          <summary style={{ cursor: 'pointer', fontWeight: 700, fontSize: 13 }}>Сырой ответ модели / usage</summary>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, marginTop: 10 }}>{JSON.stringify({ usage: result.usage }, null, 2)}</pre>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, marginTop: 10, color: '#444' }}>{result.raw}</pre>
        </details>
      )}
    </div>
  );
}
