from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from app.config import settings

THEME_CONTAINER_KEYS = {"topics", "topic", "themes", "theme", "categories", "category"}


def safe_photo_url(url: Any) -> Any:
    """Переписывает небезопасные http://<host>/... картинки на свой же origin.

    Страница открывается по HTTPS; браузер блокирует http-картинки (Mixed Content).
    Для известных хостов (settings.insecure_image_hosts) меняем `http://host` на
    `image_proxy_prefix`, а nginx фронтенда проксирует запрос на исходный хост.
    Прочие значения (https://..., пустые, относительные) возвращаем как есть.
    """
    if not isinstance(url, str) or not url:
        return url
    for host in settings.insecure_image_hosts:
        for scheme in ("http://", "https://"):
            prefix = scheme + host
            if url.startswith(prefix):
                rest = url[len(prefix):]
                if not rest.startswith("/"):
                    rest = "/" + rest
                return settings.image_proxy_prefix + rest
    return url


def json_to_theme_list(value: Any) -> list[str]:
    """Извлекает темы напрямую из колонки news_list.topics.

    В этой БД `topics` — это источник тем. Никакой allowlist/фильтрации по tag,
    extra_tag, object, regions или products здесь нет.

    Поддерживаются форматы:
    - ["Регулирование", "Экспорт"]
    - {"Регулирование": "", "Экспорт": ""}
    - {"topics": [...]}
    - "Регулирование"
    """
    return json_to_list(value)


GENERIC_JSON_KEYS = {
    "name", "title", "value", "label", "text", "region", "product", "topic", "tag", "id", "code",
    "count", "type", "url", "link", "date", "source"
}


def _is_empty_json_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _clean_token(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower() in {"null", "none", "true", "false"}:
        return None
    return text


def _add_unique(result: list[str], value: Any) -> None:
    token = _clean_token(value)
    if not token:
        return
    # Сохраняем исходный регистр первого вхождения, сравниваем без учета регистра.
    normalized = token.casefold()
    if all(x.casefold() != normalized for x in result):
        result.append(token)


def json_to_list(value: Any) -> list[str]:
    """Приводит jsonb-значения к списку человекочитаемых тегов.

    Поддерживает массивы строк, массивы объектов, одиночные строки и словари.
    В вашей базе часто встречается формат {"Линия": "", "Тренд": ""};
    в таком случае тегом является ключ, а не пустое значение.
    """
    result: list[str] = []

    def add(x: Any) -> None:
        if x is None:
            return
        if isinstance(x, str):
            _add_unique(result, x)
            return
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            _add_unique(result, x)
            return
        if isinstance(x, list):
            for item in x:
                add(item)
            return
        if isinstance(x, dict):
            # Объекты вида {name/title/value/...: "тег"}
            for key in ("name", "title", "value", "label", "text", "region", "product", "topic", "tag"):
                if key in x and not _is_empty_json_value(x.get(key)):
                    add(x[key])

            # Объекты вида {"ЦФО": "", "Технологии": ""} — значимы ключи.
            for key, child in x.items():
                key_text = _clean_token(key)
                key_is_generic = key_text is None or key_text.casefold() in GENERIC_JSON_KEYS

                if _is_empty_json_value(child):
                    if not key_is_generic:
                        _add_unique(result, key_text)
                    continue

                if isinstance(child, bool):
                    if child and not key_is_generic:
                        _add_unique(result, key_text)
                    continue

                # Для словарей-мап тегом часто является сам ключ, а значение — пояснение/вес.
                if not key_is_generic:
                    _add_unique(result, key_text)

                add(child)
            return

    add(value)
    return result


def extract_tags(*values: Any) -> list[str]:
    """Собирает единый набор тегов из tag/extra_tag/object/topics/regions/products."""
    result: list[str] = []
    for value in values:
        for tag in json_to_list(value):
            _add_unique(result, tag)
    return result


def short_text(text: str | None, limit: int = 220) -> str:
    text = (text or "").strip().replace("\n", " ")
    while "  " in text:
        text = text.replace("  ", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def row_to_news(row: dict[str, Any], *, for_list: bool = False) -> dict[str, Any]:
    dt = row.get("date")
    topics = json_to_theme_list(row.get("topics"))
    regions = json_to_list(row.get("regions"))
    products = json_to_list(row.get("products"))
    tags = extract_tags(row.get("tag"), row.get("extra_tag"), row.get("object"), topics, regions, products)
    snippet = row.get("text_snippet")
    body = row.get("text") if not for_list else None
    summary_source = snippet if for_list and snippet else body
    return {
        "id": row.get("id"),
        "id_message": row.get("id_message"),
        "date": dt.isoformat() if isinstance(dt, (datetime, date)) else dt,
        "title": row.get("title") or "Без заголовка",
        "text": "" if for_list else (body or ""),
        "summary": short_text(summary_source),
        "tag": row.get("tag"),
        "link_site": row.get("link_site"),
        "source": row.get("source"),
        "link_photo": safe_photo_url(row.get("link_photo")),
        "customer": row.get("customer"),
        "object": row.get("object"),
        "extra_tag": row.get("extra_tag"),
        "views": row.get("views") or 0,
        "subscribers": row.get("subscribers") or 0,
        "regions": regions,
        "products": products,
        "topics": topics,
        "tags": tags,
    }


def period_bounds(period: str | None) -> tuple[datetime | None, datetime | None]:
    if not period:
        return None, None

    today = date.today()
    end = datetime.combine(today + timedelta(days=1), time.min)

    if period == "today":
        start = datetime.combine(today, time.min)
    elif period == "week":
        start = datetime.combine(today - timedelta(days=6), time.min)
    elif period == "month":
        start = datetime.combine(today - timedelta(days=29), time.min)
    elif period == "quarter":
        start = datetime.combine(today - timedelta(days=89), time.min)
    else:
        return None, None

    return start, end


def facet_add(counter: dict[str, int], values: Any) -> None:
    for value in json_to_list(values):
        counter[value] = counter.get(value, 0) + 1


def facet_add_list(counter: dict[str, int], values: list[str]) -> None:
    for value in values:
        counter[value] = counter.get(value, 0) + 1


def facet_list(counter: dict[str, int], limit: int = 40) -> list[dict[str, Any]]:
    return [
        {"name": name, "count": count}
        for name, count in sorted(counter.items(), key=lambda x: (-x[1], x[0].lower()))[:limit]
    ]
