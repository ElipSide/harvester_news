"""Переписывание события в связную статью через RAGFlow.

Перед записью события worker может отправить все его источники в RAGFlow-ассистента
и получить цельную статью на русском: интересный заголовок, отражающий суть, и
полный связный текст по фактам из всех новостей события.

Дизайн:
- Ассистент не привязан к датасетам (dataset_ids=[]). Ретрив не используется — все
  источники передаются прямо в вопрос. Системный промпт требует опираться ТОЛЬКО на
  переданные тексты и вернуть строгий JSON {"title", "article"}.
- Интеграция полностью best-effort: при выключенной настройке, отсутствии SDK, ошибке
  сети/таймауте или некорректном ответе возвращаем исходный analysis без изменений —
  событие сохранится с прежним extractive-заголовком/summary.
- SDK синхронный (requests) — вызовы выполняются в отдельном потоке через asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Кэш найденного/созданного ассистента на процесс (worker — один asyncio-loop).
_chat_cache: Any = None
_chat_unavailable = False  # если SDK/инстанс недоступны — больше не пытаемся в этом процессе

# Отдельный ассистент для лаборатории промтов. RAGFlow через chats_openai игнорирует
# system-сообщение запроса и использует промт, СОХРАНЁННЫЙ в ассистенте. Поэтому, чтобы
# лаборатория реально тестировала введённый промт, мы перезаписываем сохранённый промт
# именно у этого (отдельного) ассистента — боевого, которым пишет worker, не трогаем.
_lab_chat_cache: Any = None
_lab_lock = threading.Lock()  # сериализует update-промта + генерацию (один тест-юзер)

# Полная инструкция (роль/голос/правила/ось темы) приходит в сообщении пользователя
# из реестра промтов по темам (prompts_registry). Системный промт — только жёсткий
# контракт вывода, чтобы модель не скатывалась в markdown/HTML/блоки «===».
# Мягкая формулировка: жёсткое «верни СТРОГО JSON, ничего больше, не рассуждай»
# на этой reasoning-модели для части входов провоцировало пустой ответ. Естественная
# просьба «напиши статью и дай в JSON» работает заметно стабильнее (проверено).
_SYSTEM_PROMPT = (
    "Ты — экономический обозреватель аграрного рынка. По нескольким новостям об одном "
    "событии напиши одну большую, насыщенную статью на русском — в спокойной, чуть "
    "отстранённой манере, близкой к прозе Сергея Довлатова.\n\n"
    "ОБЪЁМ И ФОРМА:\n"
    "— это полноценная статья на 500–750 слов, 6–9 абзацев, связным текстом единым потоком, "
    "без подзаголовков, списков и markdown;\n"
    "— заголовок событийный и конкретный, с главными цифрами; без кликбейта, смайликов и кавычек по краям.\n\n"
    "ГОЛОС:\n"
    "— сжато и чисто, но НЕ рублено: связанные факты соединяй в одно предложение, чередуй длину фраз, чтобы текст дышал;\n"
    "— без пафоса и общих фраз; уместны связки «впрочем», «однако», «при этом», «кстати»; концовка — 1–2 предложения, встроенные в повествование, без слова «вывод».\n\n"
    "СОДЕРЖАНИЕ:\n"
    "— раскрой событие ПОЛНО: что произошло, кто участники, где и когда, какие цифры, суммы, "
    "объёмы, сроки, причины и последствия для отрасли; разверни контекст и детали из ВСЕХ "
    "источников, не ограничивайся одним-двумя абзацами;\n"
    "— используй ТОЛЬКО факты из источников, ничего не выдумывай; цифры и единицы (млн т, тыс. т, "
    "$/т, руб./т, %) не искажай; сравнения и динамику давай, только если они выводятся из "
    "приведённых чисел, и помечай базу («год назад», «в прошлом сезоне»); если источники расходятся — отметь это.\n\n"
    'Дай ответ в формате JSON: {"title": "...", "body": "<вся статья, абзацы через \\n\\n>", "sources": "<издания через запятую>"}.'
)

# Системка для последней (plain-text) попытки — без требования JSON: на этой
# reasoning-модели именно отказ от строгого JSON разблокирует «упрямые» входы.
_PLAIN_SYSTEM = (
    "Ты пишешь развёрнутые новостные статьи для аграрного издания. Дай только заголовок и "
    "большой текст статьи на русском (500–750 слов, 6–9 абзацев), по фактам из источников, "
    "живым связным языком, без пояснений и без markdown."
)


def ragflow_active() -> bool:
    return bool(settings.ragflow_active) and not _chat_unavailable


def _get_chat() -> Any:
    """Находит/создаёт ассистента RAGFlow. Кэшируется. Бросает при недоступности."""
    global _chat_cache
    if _chat_cache is not None:
        return _chat_cache

    from ragflow_sdk import RAGFlow  # импорт здесь — SDK опционален
    from ragflow_sdk.modules.chat import Chat

    rag = RAGFlow(api_key=settings.ragflow_api_key, base_url=settings.ragflow_base_url)

    def _safe_list(**kw: Any) -> list[Any]:
        # В RAGFlow SDK list_chats бросает "The chat doesn't exist", когда совпадений нет.
        try:
            return rag.list_chats(**kw) or []
        except Exception:  # noqa: BLE001
            return []

    chat = None
    if settings.ragflow_chat_id:
        chats = _safe_list(id=settings.ragflow_chat_id)
        chat = chats[0] if chats else None

    if chat is None:
        existing = _safe_list(name=settings.ragflow_chat_name)
        if existing:
            chat = existing[0]

    if chat is None:
        llm = Chat.LLM(
            rag,
            {
                "model_name": settings.ragflow_llm_id or None,
                "temperature": settings.ragflow_temperature,
                "max_tokens": settings.ragflow_max_tokens,
            },
        )
        prompt = Chat.Prompt(
            rag,
            {
                "similarity_threshold": 0.2,
                "keywords_similarity_weight": 0.7,
                "top_n": 8,
                "top_k": 1024,
                "variables": [],
                "rerank_model": "",
                "empty_response": "",
                "opener": "",
                "show_quote": False,
                "prompt": _SYSTEM_PROMPT,
            },
        )
        chat = rag.create_chat(name=settings.ragflow_chat_name, dataset_ids=[], llm=llm, prompt=prompt)
        logger.info("RAGFlow: создан ассистент '%s' (id=%s)", settings.ragflow_chat_name, getattr(chat, "id", "?"))

    _chat_cache = chat
    return chat


def _lab_chat_name() -> str:
    return f"{settings.ragflow_chat_name} [lab]"


def _get_lab_chat() -> Any:
    """Находит/создаёт ОТДЕЛЬНОГО ассистента для лаборатории. Кэшируется на процесс."""
    global _lab_chat_cache
    if _lab_chat_cache is not None:
        return _lab_chat_cache

    from ragflow_sdk import RAGFlow  # импорт здесь — SDK опционален
    from ragflow_sdk.modules.chat import Chat

    rag = RAGFlow(api_key=settings.ragflow_api_key, base_url=settings.ragflow_base_url)
    name = _lab_chat_name()

    def _safe_list(**kw: Any) -> list[Any]:
        try:
            return rag.list_chats(**kw) or []
        except Exception:  # noqa: BLE001
            return []

    existing = _safe_list(name=name)
    chat = existing[0] if existing else None

    if chat is None:
        llm = Chat.LLM(
            rag,
            {
                "model_name": settings.ragflow_llm_id or None,
                "temperature": settings.ragflow_temperature,
                "max_tokens": settings.ragflow_max_tokens,
            },
        )
        prompt = Chat.Prompt(
            rag,
            {
                "similarity_threshold": 0.2,
                "keywords_similarity_weight": 0.7,
                "top_n": 8,
                "top_k": 1024,
                "variables": [],
                "rerank_model": "",
                "empty_response": "",
                "opener": "",
                "show_quote": False,
                "prompt": _SYSTEM_PROMPT,
            },
        )
        chat = rag.create_chat(name=name, dataset_ids=[], llm=llm, prompt=prompt)
        logger.info("RAGFlow: создан lab-ассистент '%s' (id=%s)", name, getattr(chat, "id", "?"))

    _lab_chat_cache = chat
    return chat


def _lab_set_prompt(chat: Any, sys_text: str) -> None:
    """Перезаписывает сохранённый промт lab-ассистента введённым в лаборатории текстом.

    Это и есть то, что реально влияет на генерацию (RAGFlow игнорирует system-сообщение
    запроса и берёт промт из ассистента). RAGFlow-сервер периодически кратко отказывает в
    соединении — поэтому пара коротких ретраев на сетевых сбоях."""
    import time as _time

    model_name = settings.ragflow_llm_id or getattr(getattr(chat, "llm", None), "model_name", None) or None
    payload = {
        "name": _lab_chat_name(),
        "dataset_ids": [],
        "llm": {
            "model_name": model_name,
            "temperature": settings.ragflow_temperature,
            "max_tokens": settings.ragflow_max_tokens,
        },
        "prompt": {
            "similarity_threshold": 0.2,
            "keywords_similarity_weight": 0.7,
            "top_n": 8,
            "top_k": 1024,
            "variables": [],
            "rerank_model": "",
            "empty_response": "",
            "opener": "",
            "show_quote": False,
            "prompt": sys_text,
        },
    }
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            chat.update(payload)
            return
        except Exception as exc:  # noqa: BLE001 — сетевой блип сервера RAGFlow
            last_exc = exc
            _time.sleep(1.5 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def _build_news_block(rows: list[dict[str, Any]], max_chars: int | None = None) -> str:
    """Формирует блок НОВОСТИ (все источники события) для подстановки в промт."""
    from app.services_events import _clean_text, _news_tags, row_to_news  # lazy: избегаем цикла

    blocks: list[str] = []
    max_chars = max_chars or settings.ragflow_max_source_chars
    for i, row in enumerate(rows[: settings.ragflow_max_sources], start=1):
        news = row_to_news(row)
        title = _clean_text(news.get("title"), 240)
        body = _clean_text(news.get("text") or news.get("summary"), max_chars)
        source = (news.get("source") or news.get("customer") or "").strip()
        date = row.get("date")
        date_s = date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else ""
        tags = ", ".join(_news_tags(row)[:10])
        head = f"### Источник {i}"
        meta = " · ".join(x for x in (source, date_s) if x)
        parts = [head]
        if meta:
            parts.append(meta)
        if title:
            parts.append(f"Заголовок: {title}")
        if tags:
            parts.append(f"Теги: {tags}")
        if body:
            parts.append(f"Текст: {body}")
        blocks.append("\n".join(parts))

    return "\n\n".join(blocks)


def source_previews(rows: list[dict[str, Any]], max_source_chars: int | None = None) -> list[dict[str, Any]]:
    """Источники события в том виде, как их видит модель — для лаборатории.

    Возвращает по каждому источнику структурированные поля (с учётом обрезки
    max_source_chars и порядка/лимита источников), чтобы панель показывала ровно
    тот текст, что уходит в промт."""
    from app.services_events import _clean_text, _news_tags, row_to_news  # lazy: избегаем цикла

    max_chars = max_source_chars or settings.ragflow_max_source_chars
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows[: settings.ragflow_max_sources], start=1):
        news = row_to_news(row)
        full_text = (news.get("text") or news.get("summary") or "")
        full_clean = _clean_text(full_text, 1_000_000)  # нормализуем без обрезки — для честной длины
        body = _clean_text(full_text, max_chars)
        date = row.get("date")
        out.append({
            "index": i,
            "id": int(row.get("id")) if row.get("id") is not None else None,
            "source": (news.get("source") or news.get("customer") or "").strip(),
            "date": date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else "",
            "title": _clean_text(news.get("title"), 240),
            "tags": _news_tags(row)[:10],
            "text": body,
            "link": (news.get("link_site") or "").strip(),
            "full_chars": len(full_clean),
            "shown_chars": len(body),
            "truncated": len(full_clean) > len(body),
        })
    return out


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _compose_body(body: str, sources: Any) -> str:
    """Тело статьи + строка источников в конце (если не пусто и ещё не добавлена)."""
    body = (body or "").strip()
    if isinstance(sources, (list, tuple)):
        src = ", ".join(str(s).strip() for s in sources if str(s).strip())
    else:
        src = str(sources or "").strip()
    if src and src.lower() not in body.lower():
        body = f"{body}\n\nИсточники: {src}"
    return body


def _parse_article(content: str) -> dict[str, Any] | None:
    """Извлекает статью из ответа модели максимально устойчиво.

    Контракт: {"post":{title,body,sources}, "article":{title,body,sources}}.
    Поддержан и старый плоский {"title","article"}. Возвращает
    {title, article, post?} — title/article это заголовок и тело статьи (для события),
    post — словарь поста для Telegram (сохраняется в raw_llm).
    """
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    obj = None
    try:
        obj = json.loads(text)
    except Exception:  # noqa: BLE001
        m = _JSON_RE.search(text)
        if m:
            try:
                obj = json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                obj = None

    if isinstance(obj, dict):
        # основной контракт: {"title","body","sources"}
        title = str(obj.get("title") or "").strip()
        body = _compose_body(obj.get("body") or obj.get("text") or "", obj.get("sources"))
        if body and len(body) >= 80:
            return {"title": title, "article": body}
        # совместимость: {"article": {...}} (+ опционально post)
        art = obj.get("article")
        if isinstance(art, dict):
            title = str(art.get("title") or "").strip()
            body = _compose_body(art.get("body") or art.get("text") or "", art.get("sources"))
            if body and len(body) >= 80:
                out: dict[str, Any] = {"title": title, "article": body}
                if isinstance(obj.get("post"), dict):
                    out["post"] = obj["post"]
                return out
        # совместимость: плоский {"title","article":"<текст>"}
        if isinstance(obj.get("article"), str):
            body = obj["article"].strip()
            if body and len(body) >= 80:
                return {"title": title, "article": body}

    repaired = _repair_partial(text)
    if repaired:
        return repaired

    # Plain-text fallback: ответ не JSON — первая непустая строка = заголовок, остальное = тело.
    if not text.lstrip().startswith("{"):
        lines = [ln.strip() for ln in text.splitlines()]
        nonempty = [ln for ln in lines if ln]
        if nonempty:
            title = re.sub(r"^(заголовок|title)\s*[:\-—]\s*", "", nonempty[0], flags=re.IGNORECASE).strip().strip('"«»')
            rest = text.split(nonempty[0], 1)[1].strip() if len(nonempty) > 1 else ""
            rest = re.sub(r"^(текст|статья|body)\s*[:\-—]\s*", "", rest, flags=re.IGNORECASE).strip()
            if len(rest) >= 80:
                return {"title": title, "article": rest}
    return None


def _repair_partial(text: str) -> dict[str, Any] | None:
    """Достаёт заголовок/тело статьи из неполного/битого JSON (обрыв потока)."""
    def _unescape(s: str) -> str:
        try:
            return json.loads('"' + s.rstrip("\\") + '"')
        except Exception:  # noqa: BLE001
            return s.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")

    # сначала пытаемся вытащить из вложенного "article": {...}
    art_scope = text
    am = re.search(r'"article"\s*:\s*\{(.*)$', text, re.DOTALL)
    if am:
        art_scope = am.group(1)

    bm = re.search(r'"body"\s*:\s*"((?:[^"\\]|\\.)*)"?', art_scope, re.DOTALL)
    if not bm:
        # старый контракт: "article":"<текст>"
        bm = re.search(r'"article"\s*:\s*"((?:[^"\\]|\\.)*)"?', text, re.DOTALL)
        if not bm:
            return None
    body = _unescape(bm.group(1)).strip()
    body = re.sub(r'[\s"}\]]+$', "", body).strip()
    if len(body) < 80:
        return None
    tm = re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', art_scope, re.DOTALL) or \
        re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
    title = _unescape(tm.group(1)).strip() if tm else ""
    return {"title": title, "article": body}


def _generate_sync(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Синхронный вызов RAGFlow через OpenAI-совместимый эндпоинт.

    Нативный /completions на этом инстансе либо отдаёт пустой answer, либо
    рвёт соединение через прокси. Эндпоинт /api/v1/chats_openai/{chat_id}/chat/completions
    проходит чисто. Общий дедлайн — снаружи через asyncio.wait_for; read-timeout — тут.

    Короткий универсальный промт (одна статья по всем источникам события). На случай,
    когда reasoning-модель для конкретного входа детерминированно отдаёт пустой ответ:
    ретраим с варьирующим маркером, а на последней попытке — plain-text без требования
    JSON (эта комбинация разблокирует часть «упрямых» входов).
    """
    import requests  # зависимость ragflow-sdk, всегда доступна при активной интеграции

    chat = _get_chat()
    chat_id = getattr(chat, "id", None)
    if not chat_id:
        return None

    base = settings.ragflow_base_url.rstrip("/")
    url = f"{base}/api/v1/chats_openai/{chat_id}/chat/completions"
    model = settings.ragflow_llm_id or getattr(getattr(chat, "llm", None), "model_name", None) or "model"
    news_block = _build_news_block(rows)
    user_prompt = (
        "Новости об одном событии (источники ниже). Напиши по ним одну большую насыщенную "
        f"статью (500–750 слов, 6–9 абзацев), раскрыв все факты и детали.\n\n{news_block}"
    )
    plain_prompt = (
        "Новости об одном событии (источники ниже). Напиши по ним большую статью: первая "
        "строка — заголовок, дальше с новой строки связный текст на 500–750 слов в 6–9 абзацев, "
        f"раскрой все факты и детали из источников.\n\n{news_block}"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "temperature": settings.ragflow_temperature,
        "max_tokens": settings.ragflow_max_tokens,
    }
    # Per-request read-timeout ограничиваем, чтобы в общий дедлайн уместилось
    # несколько попыток (asyncio.wait_for снаружи = ragflow_timeout_seconds).
    read_timeout = max(30, min(120, settings.ragflow_timeout_seconds - 5))

    last_usage = None
    total = settings.ragflow_max_attempts
    for attempt in range(total):
        if attempt == 0:
            body["messages"][0]["content"] = _SYSTEM_PROMPT
            body["messages"][1]["content"] = user_prompt
        elif attempt < total - 1:
            body["messages"][0]["content"] = _SYSTEM_PROMPT
            body["messages"][1]["content"] = f"{user_prompt}\n\n(Повтор {attempt}. Сразу выдай статью в JSON.)"
        else:
            # Последняя попытка: plain-text системка без требования JSON — разблокирует упрямые входы.
            body["messages"][0]["content"] = _PLAIN_SYSTEM
            body["messages"][1]["content"] = plain_prompt
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {settings.ragflow_api_key}"},
                json=body,
                timeout=(15, read_timeout),
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("RAGFlow: сетевая ошибка (%s), попытка %d", type(exc).__name__, attempt + 1)
            continue
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            content = ""
        if content:
            parsed = _parse_article(content)
            if parsed:
                return parsed
        last_usage = data.get("usage") if isinstance(data, dict) else None
        logger.warning("RAGFlow: пустой/непарсируемый content (попытка %d, usage=%s)", attempt + 1, last_usage)
    return None


def _qualifies(rows: list[dict[str, Any]]) -> bool:
    from app.services_events import row_to_news  # lazy

    names = set()
    for row in rows:
        news = row_to_news(row)
        name = (news.get("source") or news.get("customer") or "").strip()
        if name:
            names.add(name)
    distinct = len(names) or len(rows)
    return distinct >= settings.ragflow_effective_min_sources


async def rewrite_event_article(
    analysis: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Best-effort: заменяет title/summary в analysis на статью из RAGFlow.

    Возвращает тот же словарь (мутируя его). При любой проблеме — без изменений.
    """
    global _chat_unavailable
    if not ragflow_active() or not rows:
        return analysis
    if not _qualifies(rows):
        return analysis

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_generate_sync, rows),
            timeout=settings.ragflow_timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning("RAGFlow: таймаут генерации статьи (%ss)", settings.ragflow_timeout_seconds)
        return analysis
    except ModuleNotFoundError:
        logger.warning("RAGFlow: пакет ragflow_sdk не установлен — статьи не генерируются")
        _chat_unavailable = True
        return analysis
    except Exception as exc:  # noqa: BLE001
        logger.warning("RAGFlow: ошибка генерации статьи, fallback на extractive: %s", exc)
        return analysis

    if not result:
        logger.info("RAGFlow: пустой/некорректный ответ, оставляю extractive summary")
        return analysis

    if result.get("title"):
        analysis["title"] = result["title"]
    analysis["summary"] = result["article"]
    raw = analysis.get("raw_llm")
    if not isinstance(raw, dict):
        raw = {}
    raw["ragflow_used"] = True
    raw["ragflow_chat"] = settings.ragflow_chat_name
    analysis["raw_llm"] = raw
    return analysis


# ─────────────────────────────────────────────────────────────────────────────
# Промт-лаборатория: дефолты + одиночная генерация-превью БЕЗ записи в БД.
# ─────────────────────────────────────────────────────────────────────────────

def default_prompts() -> dict[str, Any]:
    """Текущие дефолтные промты — для предзаполнения формы тест-страницы."""
    return {
        "system_prompt": _SYSTEM_PROMPT,
        "plain_system": _PLAIN_SYSTEM,
        "max_source_chars": settings.ragflow_max_source_chars,
        "max_sources": settings.ragflow_max_sources,
        "model": settings.ragflow_llm_id or "(default ассистента)",
        "active": bool(settings.ragflow_active),
    }


def _preview_sync(
    rows: list[dict[str, Any]],
    system_prompt: str | None,
    user_prompt: str | None,
    max_source_chars: int | None,
) -> dict[str, Any]:
    """Одиночный вызов RAGFlow с заданным промтом. Возвращает сырой и разобранный ответ.
    НИЧЕГО не пишет в БД — только для тест-страницы."""
    import requests

    # ВАЖНО: используем ОТДЕЛЬНОГО lab-ассистента и перезаписываем его сохранённый промт
    # (см. _lab_set_prompt) — иначе RAGFlow проигнорирует введённый в лаборатории текст и
    # возьмёт промт боевого ассистента. Боевого (worker) не трогаем.
    chat = _get_lab_chat()
    chat_id = getattr(chat, "id", None)
    if not chat_id:
        return {"ok": False, "error": "no_chat"}

    base = settings.ragflow_base_url.rstrip("/")
    url = f"{base}/api/v1/chats_openai/{chat_id}/chat/completions"
    model = settings.ragflow_llm_id or getattr(getattr(chat, "llm", None), "model_name", None) or "model"
    news_block = _build_news_block(rows, max_source_chars)
    sys_text = (system_prompt or "").strip() or _SYSTEM_PROMPT
    if user_prompt and user_prompt.strip():
        usr_text = f"{user_prompt.strip()}\n\n{news_block}"
    else:
        usr_text = (
            "Новости об одном событии (источники ниже). Напиши по ним одну большую насыщенную "
            f"статью (500–750 слов, 6–9 абзацев), раскрыв все факты и детали.\n\n{news_block}"
        )
    # Plain-форма user-сообщения для ПОСЛЕДНЕЙ попытки: без требования JSON. RAGFlow
    # игнорирует system-сообщение, поэтому «разблокировка» упрямых пустых ответов идёт
    # именно через смену user-сообщения (проверено на воркере). Системный (сохранённый)
    # промт остаётся твоим — стиль/правила те же, меняется только форма вывода.
    plain_text_usr = (
        "Новости об одном событии (источники ниже). Напиши по ним статью на русском по "
        "правилам выше. ВАЖНО: НЕ используй JSON и markdown — первой строкой дай заголовок, "
        f"затем с новой строки связный текст статьи.\n\n{news_block}"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_text},
            {"role": "user", "content": usr_text},
        ],
        "stream": False,
        "temperature": settings.ragflow_temperature,
        "max_tokens": settings.ragflow_max_tokens,
    }
    # Раньше read-таймаут был зажат в 120с под лимит nginx, но лаборатория теперь
    # асинхронная (poll), поэтому даём модели полный бюджет — иначе медленные, но
    # рабочие генерации (120–250с) рубились по таймауту. Пустые ответы reasoning-модель
    # отдаёт быстро, так что место для retry на пустоту остаётся.
    read_timeout = max(30, min(300, settings.ragflow_timeout_seconds - 5))

    # Несколько попыток с варьирующим маркером (тот же системный промт) — чтобы не
    # ловить случайные пустые ответы reasoning-модели и честно проверять именно промт.
    content = ""
    usage = None
    attempts_used = 0
    used_fallback = False
    # В лаборатории даём БОЛЬШЕ попыток, чем воркеру: пустой ответ reasoning-модели
    # недетерминирован, а здесь важно почти всегда что-то показать. Последние ДВЕ попытки —
    # plain-text без JSON (эта форма пустует заметно реже), остальные — твой промт как есть.
    total = max(settings.ragflow_max_attempts, 4)
    plain_from = total - 2  # с этой попытки и далее — plain-text
    # Лок: между записью промта и POST'ом другой тест-запрос не должен перезаписать промт
    # ассистента. Покрывает все попытки (каждый POST заново читает сохранённый промт).
    with _lab_lock:
        _lab_set_prompt(chat, sys_text)
        for attempt in range(total):
            attempts_used = attempt + 1
            is_fallback_attempt = attempt >= plain_from
            if is_fallback_attempt:
                # plain-text без JSON; маркер на последней попытке варьирует ввод.
                body["messages"][1]["content"] = plain_text_usr if attempt == plain_from else f"{plain_text_usr}\n\n(Повтор {attempt}.)"
            elif attempt == 0:
                body["messages"][1]["content"] = usr_text
            else:
                body["messages"][1]["content"] = f"{usr_text}\n\n(Повтор {attempt}.)"
            try:
                resp = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {settings.ragflow_api_key}"},
                    json=body,
                    timeout=(15, read_timeout),
                )
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as exc:
                # Обрыв/отказ соединения RAGFlow-сервера — пробуем следующую попытку.
                logger.warning("lab preview: сетевая ошибка попытки %s: %s", attempts_used, exc)
                content = ""
                continue
            try:
                content = data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError):
                content = ""
            usage = data.get("usage") if isinstance(data, dict) else None
            if content and _parse_article(content):
                used_fallback = is_fallback_attempt
                break

    parsed = _parse_article(content) if content else None
    result = {
        "ok": bool(parsed),
        "title": (parsed or {}).get("title", ""),
        "article": (parsed or {}).get("article", ""),
        "words": len(((parsed or {}).get("article", "") or "").split()),
        "raw": content,
        "usage": usage,
        "attempts": attempts_used,
        "used_fallback": used_fallback,
        "model": model,
        "sources_used": min(len(rows), settings.ragflow_max_sources),
    }
    if not parsed:
        # Различаем настоящий пустой ответ модели и непарсящийся текст — для понятного UI.
        result["error"] = "model_empty" if not (content or "").strip() else "unparsable"
    return result


async def preview_article(
    rows: list[dict[str, Any]],
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    max_source_chars: int | None = None,
) -> dict[str, Any]:
    """Async-обёртка превью-генерации. Без записи в БД. Возвращает результат или ошибку."""
    if not settings.ragflow_active:
        return {"ok": False, "error": "ragflow_inactive"}
    if not rows:
        return {"ok": False, "error": "no_sources"}
    # reasoning-модель недетерминирована: один заход ~150–220с, и часть из них —
    # пустой ответ (всё «думанье» без текста). Спасает только ретрай, поэтому даём
    # лаборатории бюджет под ВСЕ попытки целиком — иначе outer-timeout рубит 2-ю/3-ю.
    # Лаборатория асинхронная (poll), длинное ожидание здесь приемлемо.
    per_attempt = max(30, min(300, settings.ragflow_timeout_seconds - 5))
    lab_attempts = max(settings.ragflow_max_attempts, 4)  # синхронно с _preview_sync
    lab_timeout = lab_attempts * per_attempt + 30
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_preview_sync, rows, system_prompt, user_prompt, max_source_chars),
            timeout=lab_timeout,
        )
    except asyncio.TimeoutError:
        return {"ok": False, "error": "timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
