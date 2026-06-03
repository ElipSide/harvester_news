from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import logging
import math
import re
from typing import Any

from app.config import settings
from app.services_news import _role_impacts
from app.services_events import (
    _as_str_list,
    _best_title,
    _clean_text,
    _date_gap_days,
    _facet_from_records,
    _jaccard,
    _news_record,
    _sigma,
    _split_sentences,
    _tokens,
)

logger = logging.getLogger(__name__)

_MODEL = None
_MODEL_ERROR: str | None = None

_GENERIC_TITLE_TOKENS = {
    "новости", "новость", "россии", "россия", "рынок", "рынка", "компания", "компании", "данные", "итоги",
    "обзор", "эксперты", "агро", "апк", "сегодня", "вчера", "заявил", "сообщил", "сообщили", "опубликовал",
    "опубликован", "информация", "инфляция", "апреле", "мае", "июне", "года", "день", "неделя",
    "главное", "главные", "самое", "интересное", "дайджест", "подборка", "канал", "подписаться",
    "главпахарь", "главагроном", "олеоскоп", "агротренд", "агроновости", "минсельхоз",
    "россельхознадзор", "фгбу", "вниикр", "нтб", "торгов", "торги", "результаты", "состоявшихся",
    "2024", "2025", "2026",
}

_DIGEST_TITLE_PATTERNS = (
    "дайджест",
    "самое интересное за день",
    "самое интересное за неделю",
    "главные новости",
    "новости рынка на",
    "итоги дня",
    "итоги недели",
    "подборка новостей",
    "результаты состоявшихся торгов",
    "главпахарь: самое интересное",
    "главагроном: самое интересное",
)

_SERVICE_PHRASES = (
    "telegram | max",
    "культиватор 👻 в max",
    "подписывайтесь",
    "перейдя по ссылке",
)


def semantic_available() -> bool:
    if not settings.semantic_rag_enabled:
        return False
    model = _get_model()
    return model is not None


def _get_model():
    global _MODEL, _MODEL_ERROR
    if _MODEL is not None:
        return _MODEL
    if _MODEL_ERROR is not None:
        return None
    if not settings.semantic_rag_enabled:
        _MODEL_ERROR = "semantic rag disabled"
        return None
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        logger.info("loading semantic model: %s", settings.semantic_embedding_model)
        _MODEL = SentenceTransformer(settings.semantic_embedding_model, device=settings.semantic_device)
        logger.info("semantic model loaded")
        return _MODEL
    except Exception as exc:  # pragma: no cover - runtime fallback
        _MODEL_ERROR = str(exc)
        logger.exception("semantic model unavailable, fallback to offline-rag: %s", exc)
        return None


def _safe_norm(vec: list[float]) -> list[float]:
    s = math.sqrt(sum(float(x) * float(x) for x in vec))
    if s <= 0:
        return [0.0 for _ in vec]
    return [float(x) / s for x in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def _strip_emoji_and_noise(text: str) -> str:
    text = re.sub(r"[\U00010000-\U0010ffff]", " ", text or "")
    text = re.sub(r"[#*_`>•▪▫🔹🔸☑️✅❗️]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _lead_text(text: str, max_len: int = 520) -> str:
    text = _strip_emoji_and_noise(text or "")
    # Не даём длинным дайджестам и теговым хвостам доминировать embedding'ом.
    parts = _split_sentences(text)[:3]
    if parts:
        return _clean_text(" ".join(parts), max_len)
    return _clean_text(text, max_len)


def _record_text(rec: dict[str, Any]) -> str:
    title = _strip_emoji_and_noise(rec.get("title") or "")
    text = _lead_text(rec.get("text") or "", 560)
    # В embedding сознательно НЕ добавляем все теги/регионы/продукты: они создавали
    # ложную близость и склеивали разные новости одной отраслевой тематики.
    return _clean_text(f"query: {title}. {title}. {text}", 1100)


def _embed_texts(texts: list[str]) -> list[list[float]]:
    model = _get_model()
    if model is None:
        raise RuntimeError(_MODEL_ERROR or "semantic model unavailable")
    emb = model.encode(
        texts,
        batch_size=settings.semantic_batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    # sentence-transformers может вернуть numpy.ndarray или список.
    return [[float(x) for x in row] for row in emb]


def _title_anchor_tokens(rec: dict[str, Any]) -> set[str]:
    toks = set(_tokens(rec.get("title") or "", 40))
    return {t for t in toks if t not in _GENERIC_TITLE_TOKENS and not t.isdigit()}


def _tag_set(rec: dict[str, Any]) -> set[str]:
    # Убираем слишком общие теги из логики склейки. Они остаются в карточке, но не
    # должны быть причиной объединения разных событий.
    generic = {
        "россия", "аналитика", "цфо", "юфо", "сфо", "пфо", "скфо", "сзфо", "уфо", "дфо",
        "европа", "азия", "северная америка", "технологии", "мероприятия", "линия", "прочее",
        "зерновые", "масличные", "зернобобовые",
    }
    return {
        str(t).casefold().replace("ё", "е")
        for t in (rec.get("tags") or [])
        if str(t).strip() and str(t).casefold().replace("ё", "е") not in generic
    }


def _normalized_title(text: str) -> str:
    text = _strip_emoji_and_noise(text).casefold().replace("ё", "е")
    text = re.sub(r"https?://\S+|t\.me/\S+", " ", text)
    text = re.sub(r"[^a-zа-я0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_digest_like(rec: dict[str, Any]) -> bool:
    title = _normalized_title(rec.get("title") or "")
    text = _normalized_title((rec.get("text") or "")[:900])
    hay = f"{title} {text}"
    if any(pat in hay for pat in _DIGEST_TITLE_PATTERNS):
        return True
    # Часто такие посты — это агрегаторы с большим количеством несвязанных тегов.
    if len(rec.get("tags") or []) >= 18 and any(w in title for w in ("главпахарь", "главагроном", "дайджест", "итоги")):
        return True
    # Дайджесты обычно содержат несколько маркированных пунктов и ссылочные хвосты.
    if sum(text.count(x) for x in ("*", "🔹", "🔸", "▪", "•", "☑")) >= 4:
        return True
    if any(p in hay for p in _SERVICE_PHRASES) and len(rec.get("tags") or []) >= 12:
        return True
    return False


def _title_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    return _jaccard(_title_anchor_tokens(a), _title_anchor_tokens(b))


def _can_link(a: dict[str, Any], b: dict[str, Any], sem: float) -> tuple[bool, float, str]:
    gap = _date_gap_days(a.get("date"), b.get("date"))
    if gap > settings.semantic_cluster_window_days:
        return False, 0.0, "date_gap"

    title_a = _title_anchor_tokens(a)
    title_b = _title_anchor_tokens(b)
    tags_a = _tag_set(a)
    tags_b = _tag_set(b)
    token_score = _jaccard(set(a.get("tokens") or []), set(b.get("tokens") or []))
    title_score = _jaccard(title_a, title_b)
    tag_score = _jaccard(tags_a, tags_b)
    shared_anchor = bool(title_a & title_b)
    shared_tag = bool(tags_a & tags_b)
    digest_pair = bool(a.get("digest_like") or b.get("digest_like"))

    # Дайджесты/подборки почти всегда содержат много разных событий. Они могут
    # соединяться только с почти таким же заголовком, иначе становятся мостом для
    # огромного мусорного кластера.
    if digest_pair and title_score < 0.55:
        return False, sem, "digest_guard"

    if sem < settings.semantic_cluster_min_cosine:
        return False, sem, "low_cosine"

    # Нельзя склеивать две отраслевые новости только потому, что они обе про АПК.
    # Нужен текстовый/заголовочный якорь: одно и то же название, объект, мера, событие.
    has_title_evidence = title_score >= settings.semantic_min_title_overlap or shared_anchor
    has_token_evidence = token_score >= settings.semantic_min_token_overlap

    if sem >= settings.semantic_cluster_strong_cosine and (has_title_evidence or has_token_evidence):
        score = 0.70 * sem + 0.18 * title_score + 0.08 * token_score + 0.04 * tag_score
        return True, score, "strong_title_semantic"

    if has_title_evidence and has_token_evidence:
        score = 0.66 * sem + 0.20 * title_score + 0.10 * token_score + 0.04 * tag_score
        return True, score, "title_token_semantic"

    # Один общий тег без совпадающих заголовочных якорей больше не достаточен.
    if shared_tag and title_score >= max(0.22, settings.semantic_min_title_overlap) and token_score >= 0.14:
        score = 0.62 * sem + 0.18 * title_score + 0.10 * token_score + 0.10 * tag_score
        return True, score, "tag_with_title"

    return False, sem, "no_event_anchor"


class _DSU:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.size = [1] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]


def _cluster_cohesion(candidate: dict[str, Any], cluster: list[dict[str, Any]]) -> float:
    if not cluster:
        return 1.0
    vals = [_cosine(candidate.get("embedding") or [], item.get("embedding") or []) for item in cluster]
    return sum(vals) / max(1, len(vals))


def _best_cluster_for_record(rec: dict[str, Any], clusters: list[list[dict[str, Any]]]) -> tuple[int, float, str]:
    best_idx = -1
    best_score = 0.0
    best_reason = ""
    for idx, cluster in enumerate(clusters):
        if len(cluster) >= max(2, settings.semantic_max_cluster_size):
            continue
        cohesion = _cluster_cohesion(rec, cluster)
        if cohesion < settings.semantic_min_cluster_cohesion:
            continue
        # Кандидат должен быть связан не просто с одним случайным элементом,
        # а с seed/центром кластера. Это убирает transitive-chain overmerge.
        checks = cluster[: min(4, len(cluster))]
        ok_links: list[tuple[float, str]] = []
        for item in checks:
            sem = _cosine(rec.get("embedding") or [], item.get("embedding") or [])
            ok, score, reason = _can_link(rec, item, sem)
            if ok:
                ok_links.append((score, reason))
        if not ok_links:
            continue
        # Для кластера из 3+ источников требуем хотя бы две связи или очень сильную одну.
        ok_links.sort(reverse=True)
        score, reason = ok_links[0]
        if len(cluster) >= 3 and len(ok_links) < 2 and score < 0.86:
            continue
        score = 0.82 * score + 0.18 * cohesion
        if score > best_score:
            best_idx = idx
            best_score = score
            best_reason = reason
    return best_idx, best_score, best_reason


def cluster_news_rows_semantic(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    records = [_news_record(r) for r in rows]
    records = [r for r in records if r.get("title") or r.get("text")]
    if not records:
        return []

    records.sort(key=lambda r: (r.get("date") or datetime.min, r.get("views") or 0, r.get("id") or 0), reverse=True)
    for rec in records:
        rec["digest_like"] = _is_digest_like(rec)

    # Digest/list posts не должны становиться источниками события. Иначе они
    # мостят разные новости: выставки, торги, карантин, цены и аналитику в один кластер.
    if settings.semantic_exclude_digest_sources:
        primary_records = [r for r in records if not r.get("digest_like")]
    else:
        primary_records = records
    if not primary_records:
        primary_records = records[:1]

    texts = [_record_text(r) for r in primary_records]
    vectors = _embed_texts(texts)
    for rec, vec in zip(primary_records, vectors):
        rec["embedding"] = _safe_norm(vec)

    clusters: list[list[dict[str, Any]]] = []
    for rec in primary_records:
        idx, score, reason = _best_cluster_for_record(rec, clusters)
        if idx >= 0:
            clusters[idx].append(rec)
        else:
            clusters.append([rec])

    # Финальный guard: если внутри кластера оказались несколько разных заголовочных
    # ядер, раскалываем его по ближайшему seed. Это дешевле и стабильнее, чем DSU.
    refined: list[list[dict[str, Any]]] = []
    for cluster in clusters:
        if len(cluster) <= 2:
            refined.append(cluster)
            continue
        subclusters: list[list[dict[str, Any]]] = []
        for rec in cluster:
            idx, _, _ = _best_cluster_for_record(rec, subclusters)
            if idx >= 0:
                subclusters[idx].append(rec)
            else:
                subclusters.append([rec])
        refined.extend(subclusters)

    refined.sort(key=lambda cl: (len(cl), max((r.get("views") or 0) for r in cl), max((r.get("date") or datetime.min) for r in cl)), reverse=True)

    out: list[list[dict[str, Any]]] = []
    for cluster in refined:
        cluster.sort(key=lambda r: (r.get("views") or 0, r.get("date") or datetime.min), reverse=True)
        out.append([r["row"] for r in cluster])
    return out


def _centroid(records: list[dict[str, Any]]) -> list[float]:
    vecs = [r.get("embedding") for r in records if r.get("embedding")]
    if not vecs:
        return []
    dims = len(vecs[0])
    c = [0.0] * dims
    for v in vecs:
        for i, x in enumerate(v):
            c[i] += float(x)
    return _safe_norm(c)


def _best_semantic_title(records: list[dict[str, Any]]) -> str:
    c = _centroid(records)
    if not c:
        return _best_title(records)

    def score(rec: dict[str, Any]) -> float:
        title = rec.get("title") or ""
        if len(title) < 12:
            return -9999
        sem = _cosine(rec.get("embedding") or [], c)
        length_bonus = 0.25 if 35 <= len(title) <= 150 else 0.0
        source_bonus = 0.08 if rec.get("source") else 0.0
        views_bonus = min(0.25, math.log1p(rec.get("views") or 0) / 30)
        return sem + length_bonus + source_bonus + views_bonus

    return max(records, key=score).get("title") or _best_title(records)


def _sentence_candidates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rec in records[: settings.event_context_sources_limit]:
        if _is_digest_like(rec):
            continue
        for sent in _split_sentences(rec.get("text") or ""):
            if len(sent) < 55 or len(sent) > 420:
                continue
            # Не берём чистую рекламу/служебные хвосты.
            low = sent.casefold()
            if low.count("http") or "telegram | max" in low or "подписаться" in low:
                continue
            out.append({"sentence": sent, "source": rec.get("source"), "views": rec.get("views") or 0})
    return out


def _semantic_summary(records: list[dict[str, Any]]) -> str:
    cands = _sentence_candidates(records)
    if not cands:
        # Фолбэк из старого extractive summarizer без импортного цикла.
        snippets = [_clean_text(r.get("text"), 260) for r in records[:2] if r.get("text")]
        return _clean_text(" ".join(snippets), 900)

    texts = ["passage: " + c["sentence"] for c in cands]
    try:
        vecs = [_safe_norm(v) for v in _embed_texts(texts)]
    except Exception:
        vecs = []
    rec_centroid = _centroid(records)

    scored: list[tuple[float, str, set[str]]] = []
    for idx, cand in enumerate(cands):
        sent = cand["sentence"]
        toks = set(_tokens(sent, 80))
        sem = _cosine(vecs[idx], rec_centroid) if vecs and rec_centroid else 0.0
        length_bonus = 0.12 if 90 <= len(sent) <= 260 else 0.0
        source_bonus = 0.06 if cand.get("source") else 0.0
        views_bonus = min(0.08, math.log1p(cand.get("views") or 0) / 80)
        scored.append((sem + length_bonus + source_bonus + views_bonus, sent, toks))
    scored.sort(key=lambda x: x[0], reverse=True)

    selected: list[str] = []
    selected_toks: list[set[str]] = []
    for _, sent, toks in scored:
        if not toks:
            continue
        if any(_jaccard(toks, prev) > 0.55 for prev in selected_toks):
            continue
        selected.append(sent)
        selected_toks.append(toks)
        if len(selected) >= 3:
            break
    return _clean_text(" ".join(selected), 900)


def _semantic_sigma(records: list[dict[str, Any]]) -> int:
    base = _sigma(records)
    if len(records) <= 1:
        return min(base, 68)
    vecs = [r.get("embedding") for r in records if r.get("embedding")]
    if len(vecs) < 2:
        return base
    pairs = []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            pairs.append(_cosine(vecs[i], vecs[j]))
    cohesion = sum(pairs) / max(1, len(pairs))
    # Сильно штрафуем слабую семантическую связность.
    if cohesion < 0.55:
        return max(45, base - 16)
    if cohesion > 0.78:
        return min(97, base + 5)
    return min(94, base)


async def analyze_group_semantic_rag(event_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    records = [_news_record(r) for r in rows]
    for rec in records:
        rec["digest_like"] = _is_digest_like(rec)
    texts = [_record_text(r) for r in records]
    try:
        vecs = _embed_texts(texts)
        for rec, vec in zip(records, vecs):
            rec["embedding"] = _safe_norm(vec)
    except Exception as exc:
        logger.warning("semantic analysis fallback for event %s: %s", event_id, exc)
        from app.services_events import analyze_group_offline_rag

        return await analyze_group_offline_rag(event_id, rows)

    tag_counter: Counter[str] = Counter()
    token_counter: Counter[str] = Counter()
    for rec in records:
        tag_counter.update(rec.get("tags") or [])
        token_counter.update(rec.get("token_list") or [])

    tags = [tag for tag, _ in tag_counter.most_common(30)]
    topics = _facet_from_records(records, "topics", 20) or tags[:10]
    regions = _facet_from_records(records, "regions", 20)
    products = _facet_from_records(records, "products", 20)
    title = _best_semantic_title(records)
    summary = _semantic_summary(records)
    impacts = _role_impacts(tags + topics + regions + products, f"{title} {summary}")

    context_sources = []
    for rec in records[: settings.event_context_sources_limit]:
        context_sources.append(
            {
                "id": rec["id"],
                "date": rec["date"].isoformat() if isinstance(rec.get("date"), datetime) else None,
                "title": rec["title"],
                "source": rec.get("source"),
                "snippet": _clean_text(rec.get("text"), 500),
                "tags": rec.get("tags", [])[:16],
            }
        )

    return {
        "title": title,
        "summary": summary,
        "tags": tags,
        "topics": topics,
        "regions": regions,
        "products": products,
        "impacts": impacts,
        "sigma": _semantic_sigma(records),
        "raw_llm": {
            "semantic_rag": True,
            "paid_llm_used": False,
            "analysis_mode": settings.event_analysis_mode,
            "embedding_model": settings.semantic_embedding_model,
            "retrieval": "local multilingual sentence-transformer embeddings + strict title-anchor guarded clustering",
            "summary": "semantic extractive MMR",
            "guards": {
                "digest_sources_excluded": settings.semantic_exclude_digest_sources,
                "min_title_overlap": settings.semantic_min_title_overlap,
                "min_cluster_cohesion": settings.semantic_min_cluster_cohesion,
                "max_cluster_size": settings.semantic_max_cluster_size,
            },
            "context_sources": context_sources,
            "top_tokens": [w for w, _ in token_counter.most_common(24)],
        },
    }
