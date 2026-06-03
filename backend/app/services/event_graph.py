"""Предрасчёт сюжетного графа событий (порт soft.py из примера).

Строит связи между событиями (рёбра) и нарезает граф на сюжеты-деревья жадным
ветвящимся лесом. Запускается воркером после сборки событий (process_events_once).

Сила связи = WE * IDF-перекрытие граней + WT * TF-IDF сходство текста, в окне WIN дней.
Результат пишется в harvester_news.event_links / event_stories и колонки events.story_*.
"""
from __future__ import annotations

import hashlib
import logging
import math
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

from psycopg import sql

from app.config import settings
from app.db.db_ext import get_conn
from app.db.db_ext_func import fetch_all
from app.services.event_tables import ensure_event_schema, event_table_identifier
from app.services_events import _STOPWORDS, _TOKEN_RE, _as_str_list

logger = logging.getLogger(__name__)

# Эпоха недельных корзин — понедельник 2020-01-06 (совпадает с WEEK_EPOCH на фронте).
_WEEK_EPOCH = date(2020, 1, 6)

# Параметры (порт soft.py)
WIN_DAYS = 21
WE, WT = 0.6, 0.4          # вклад граней / текста
TH = 0.34                  # порог ребра
SIZE_CAP = 12              # макс. размер дерева-сюжета
CHILD_CAP = 4              # макс. детей у узла
MIN_STORY = 3              # мин. длина сюжета
TOPK_LINKS = 16            # макс. рёбер на узел в хранилище

_CH_COLOR = {"P": "#1B7A3E", "G": "#D97706", "T": "#1E4FB0"}
_PAL = [
    "#534AB7", "#1B7A3E", "#B45309", "#1E4FB0", "#A1361B", "#6E5BD6",
    "#0F766E", "#9B5510", "#7A6BB0", "#2F7E8E", "#C26B3C", "#5C8C3A",
]
# Generic-темы, которые не годятся в имя сюжета (слишком общие).
_GENERIC = {"Экспорт", "Импорт", "Регулирование", "Аналитика", "Цена", "Господдержка",
            "Торговля", "Логистика", "Мероприятия", "Россия", "Прочее"}


def _text_tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS]


def _bucket_keys(d: date | None) -> tuple[str | None, str | None]:
    """Ключи недели/месяца для даты (совпадают с getBucketKey на фронте)."""
    if d is None:
        return None, None
    week = f"W{(d - _WEEK_EPOCH).days // 7}"
    month = f"{d.year:04d}-{d.month:02d}"
    return week, month


async def rebuild_event_graph_rows() -> dict[str, Any]:
    """Пересобирает предрасчётную проекцию событий для SVG-графа на главной.

    Берёт ровно те события, что отдаёт /events/graph по умолчанию
    (status='active' AND sources_count >= event_min_sources), чистит грани один раз
    и кладёт их как TEXT[] вместе с ключами недели/месяца. Эндпоинт затем читает
    эту таблицу без JSONB-парсинга на лету.
    """
    await ensure_event_schema()
    events_t = event_table_identifier("events")
    egr_t = event_table_identifier("event_graph_rows")

    rows = await fetch_all(
        sql.SQL(
            """
            SELECT id, date_from, date_to, topics, regions, products, sigma, sources_count
            FROM {events}
            WHERE status = 'active' AND sources_count >= %(min_src)s
            """
        ).format(events=events_t),
        {"min_src": settings.event_min_sources},
    )

    out: list[tuple] = []
    for r in rows:
        df = r.get("date_from")
        dt = r.get("date_to")
        d_from = df.date() if isinstance(df, datetime) else None
        d_to = dt.date() if isinstance(dt, datetime) else None
        week, month = _bucket_keys(d_from)
        out.append((
            int(r["id"]), d_from, d_to, week, month,
            _as_str_list(r.get("topics"), 20),
            _as_str_list(r.get("regions"), 20),
            _as_str_list(r.get("products"), 20),
            int(r.get("sigma") or 0),
            int(r.get("sources_count") or 0),
        ))

    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql.SQL("TRUNCATE {}").format(egr_t))
            if out:
                await cur.executemany(
                    sql.SQL(
                        "INSERT INTO {egr} "
                        "(event_id, date_from, date_to, week_key, month_key, topics, regions, products, sigma, sources_count) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                    ).format(egr=egr_t),
                    out,
                )
        await conn.commit()

    return {"rows": len(out)}


async def rebuild_event_graph() -> dict[str, Any]:
    """Пересчитывает граф связей и сюжеты по текущим активным событиям."""
    await ensure_event_schema()
    events_t = event_table_identifier("events")
    links_t = event_table_identifier("event_links")
    stories_t = event_table_identifier("event_stories")

    rows = await fetch_all(
        sql.SQL(
            """
            SELECT id, date_from, title, summary, topics, regions, products
            FROM {events}
            WHERE status = 'active' AND date_from IS NOT NULL
            ORDER BY date_from, id
            """
        ).format(events=events_t)
    )
    N = len(rows)
    if N == 0:
        return {"events": 0, "edges": 0, "stories": 0}

    # ── векторы граней и текста + IDF ──
    evs: list[dict[str, Any]] = []
    df: Counter = Counter()      # document frequency токенов граней
    tdf: Counter = Counter()     # document frequency токенов текста
    for r in rows:
        p = set(_as_str_list(r.get("products"), 40))
        g = set(_as_str_list(r.get("regions"), 40))
        c = set(_as_str_list(r.get("topics"), 40))
        toks = _text_tokens(f"{r.get('title') or ''} {r.get('summary') or ''}")
        tf = Counter(toks)
        evs.append({"id": int(r["id"]), "day": r["date_from"], "p": p, "g": g, "c": c,
                    "tf": tf, "facets": p | g | c})
        for x in (p | g | c):
            df[x] += 1
        for t in set(toks):
            tdf[t] += 1

    idf = {x: math.log((N + 1) / (df[x] + 1)) + 1 for x in df}
    tidf = {t: math.log((N + 1) / (tdf[t] + 1)) + 1 for t in tdf}
    for e in evs:
        vec = {t: (1 + math.log(f)) * tidf.get(t, 0.0) for t, f in e["tf"].items()}
        e["tvec"] = vec
        e["tnorm"] = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        e["fnorm"] = math.sqrt(sum(idf.get(x, 1.0) ** 2 for x in e["facets"])) or 1.0

    def soft(a: dict, b: dict) -> float:
        shared = a["facets"] & b["facets"]
        if not shared:
            return 0.0
        num = sum(idf.get(x, 1.0) ** 2 for x in shared)
        return num / (a["fnorm"] * b["fnorm"])

    def txt(a: dict, b: dict) -> float:
        va, vb = (a["tvec"], b["tvec"]) if len(a["tvec"]) <= len(b["tvec"]) else (b["tvec"], a["tvec"])
        if not va or not vb:
            return 0.0
        s = 0.0
        for t, w in va.items():
            wb = vb.get(t)
            if wb:
                s += w * wb
        return s / (a["tnorm"] * b["tnorm"])

    def channel(a: dict, b: dict) -> tuple[str, str | None]:
        sp = a["p"] & b["p"]
        if sp:
            return "P", sorted(sp)[0]
        sg = a["g"] & b["g"]
        if sg:
            return "G", sorted(sg)[0]
        sc = a["c"] & b["c"]
        if sc:
            return "T", sorted(sc)[0]
        return "T", None

    # ── рёбра в окне (sweep по отсортированным по дате событиям) ──
    edges: list[tuple[int, int, float, str, str | None]] = []  # (x_earlier, y_later, w, ch, lab)
    for i in range(N):
        di = evs[i]["day"]
        for j in range(i + 1, N):
            dd = (evs[j]["day"] - di).days
            if dd > WIN_DAYS:
                break
            se = soft(evs[i], evs[j])
            if se == 0.0:
                continue
            s = WE * se + WT * txt(evs[i], evs[j])
            if s < TH:
                continue
            ch, lab = channel(evs[i], evs[j])
            edges.append((i, j, round(s, 4), ch, lab))

    # ── жадный ветвящийся лес → сюжеты (порт soft.py) ──
    parent: dict[int, int] = {}
    children: dict[int, list[int]] = defaultdict(list)
    comp_size: dict[int, int] = {}

    def root(n: int) -> int:
        while n in parent:
            n = parent[n]
        return n

    for x, y, w, _ch, _lab in sorted(edges, key=lambda e: -e[2]):
        if y in parent:
            continue
        if len(children[x]) >= CHILD_CAP:
            continue
        rx = root(x)
        if rx == y:
            continue
        sx = comp_size.get(rx, 1)
        sy = comp_size.get(y, 1)
        if sx + sy > SIZE_CAP:
            continue
        parent[y] = x
        children[x].append(y)
        comp_size[rx] = sx + sy

    groups: dict[int, list[int]] = defaultdict(list)
    for n in set(parent) | set(children):
        groups[root(n)].append(n)

    # story_idx по членам; сюжеты длиной >= MIN_STORY
    member_story: dict[int, int] = {}     # idx события → индекс сюжета
    stories: list[dict[str, Any]] = []
    for members in groups.values():
        if len(members) < MIN_STORY:
            continue
        members_sorted = sorted(members, key=lambda n: (evs[n]["day"], evs[n]["id"]))
        sidx = len(stories)
        for nd in members_sorted:
            member_story[nd] = sidx
        # имя: топ-продукт · топ-тема среди членов (без generic)
        prod_c: Counter = Counter()
        top_c: Counter = Counter()
        for nd in members_sorted:
            for x in evs[nd]["p"]:
                prod_c[x] += 1
            for x in evs[nd]["c"]:
                if x not in _GENERIC:
                    top_c[x] += 1
        name_parts = []
        if prod_c:
            name_parts.append(prod_c.most_common(1)[0][0])
        if top_c:
            name_parts.append(top_c.most_common(1)[0][0])
        name = " · ".join(name_parts) if name_parts else "Сюжет"
        ids = [evs[n]["id"] for n in members_sorted]
        story_key = "s:" + hashlib.md5(",".join(map(str, sorted(ids))).encode()).hexdigest()[:16]
        stories.append({
            "key": story_key,
            "name": name,
            "color": _PAL[sidx % len(_PAL)],
            "size": len(members_sorted),
            "date_from": evs[members_sorted[0]]["day"],
            "date_to": evs[members_sorted[-1]]["day"],
            "members": members_sorted,
        })

    # ── top-K рёбер на узел + пометка сюжетных рёбер ──
    incident: dict[int, list[int]] = defaultdict(list)  # node idx → список индексов в edges
    for ei, (x, y, w, _ch, _lab) in enumerate(edges):
        incident[x].append(ei)
        incident[y].append(ei)
    keep: set[int] = set()
    for node, eis in incident.items():
        eis.sort(key=lambda ei: -edges[ei][2])
        for ei in eis[:TOPK_LINKS]:
            keep.add(ei)
    story_edge = {(x, y) for y, x in parent.items()}  # (earlier x, later y) pairs

    link_rows = []
    for ei in keep:
        x, y, w, ch, lab = edges[ei]
        link_rows.append((evs[x]["id"], evs[y]["id"], w, ch, lab, (x, y) in story_edge))

    # ── запись в БД (одна транзакция) ──
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql.SQL("TRUNCATE {}").format(links_t))
            await cur.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY").format(stories_t))
            await cur.execute(
                sql.SQL("UPDATE {events} SET story_id=NULL, story_parent_id=NULL, story_pos=NULL "
                        "WHERE story_id IS NOT NULL").format(events=events_t)
            )

            # сюжеты + привязка событий
            ev_updates = []
            for st in stories:
                await cur.execute(
                    sql.SQL(
                        "INSERT INTO {stories} (story_key,name,color,size,date_from,date_to) "
                        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id"
                    ).format(stories=stories_t),
                    (st["key"], st["name"], st["color"], st["size"], st["date_from"], st["date_to"]),
                )
                sid = (await cur.fetchone())["id"]
                for pos, nd in enumerate(st["members"]):
                    par = parent.get(nd)
                    parent_event_id = evs[par]["id"] if par is not None and par in member_story else None
                    ev_updates.append((sid, parent_event_id, pos, evs[nd]["id"]))
            if ev_updates:
                await cur.executemany(
                    sql.SQL("UPDATE {events} SET story_id=%s, story_parent_id=%s, story_pos=%s "
                            "WHERE id=%s").format(events=events_t),
                    ev_updates,
                )

            if link_rows:
                await cur.executemany(
                    sql.SQL("INSERT INTO {links} (from_id,to_id,weight,channel,lab,in_story) "
                            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (from_id,to_id) DO NOTHING").format(links=links_t),
                    link_rows,
                )
        await conn.commit()

    # Проекция для графа на главной — обновляем тем же прогоном.
    graph_rows = await rebuild_event_graph_rows()

    result = {"events": N, "edges_total": len(edges), "edges_stored": len(link_rows),
              "stories": len(stories), "events_in_story": len(member_story),
              "graph_rows": graph_rows.get("rows", 0)}
    logger.info("event graph rebuilt: %s", result)
    return result
