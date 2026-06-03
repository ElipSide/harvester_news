"""
Скрипт для загрузки фоновых изображений для каждой темы с Pixabay.

Запускать ЛОКАЛЬНО (где Pixabay не блокирует):
    cd backend
    python scripts/download_topic_images.py

Изображения сохраняются в app/services/topic_images/<topic>.jpg
После загрузки — скопировать папку на сервер или пересобрать Docker-образ.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Добавляем app в путь
sys.path.insert(0, str(Path(__file__).parent.parent))
from app.services.card_warmer import _TOPIC_QUERIES, _DEFAULT_QUERY

OUTPUT_DIR = Path(__file__).parent.parent / "app" / "services" / "topic_images"
OUTPUT_DIR.mkdir(exist_ok=True)

# Читаем ключ из .env
def _read_api_key() -> str:
    env_path = Path(__file__).parent.parent.parent / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("PIXABAY_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"\'')
    raise ValueError("PIXABAY_API_KEY not found in .env")


def fetch_pixabay(query: str, api_key: str) -> bytes | None:
    encoded = urllib.parse.quote(query)
    url = (
        f"https://pixabay.com/api/?key={api_key}"
        f"&q={encoded}"
        f"&image_type=photo&orientation=horizontal"
        f"&min_width=900&min_height=600"
        f"&safesearch=true&per_page=5&order=popular"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "HarvesterNews/2.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    hits = data.get("hits") or []
    if not hits:
        return None
    photo_url = hits[0].get("largeImageURL") or hits[0].get("webformatURL")
    if not photo_url:
        return None
    req2 = urllib.request.Request(photo_url, headers={"User-Agent": "HarvesterNews/2.0"})
    with urllib.request.urlopen(req2, timeout=20) as resp:
        return resp.read()


def safe_filename(topic: str) -> str:
    # Убираем символы недопустимые в именах файлов
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in topic).strip()


def main() -> None:
    api_key = _read_api_key()
    print(f"API key: {api_key[:10]}...")

    # Все темы + дефолтный запрос
    all_queries: dict[str, str] = {**_TOPIC_QUERIES, "__default__": _DEFAULT_QUERY}

    ok, skip, fail = 0, 0, 0

    for topic, query in all_queries.items():
        filename = OUTPUT_DIR / f"{safe_filename(topic)}.jpg"
        if filename.exists():
            print(f"  SKIP  {topic}")
            skip += 1
            continue

        print(f"  GET   {topic!r} -> {query!r}", end=" ... ", flush=True)
        try:
            data = fetch_pixabay(query, api_key)
            if data:
                filename.write_bytes(data)
                print(f"OK ({len(data) // 1024} KB)")
                ok += 1
            else:
                print("no results")
                fail += 1
        except Exception as e:
            print(f"FAIL: {e}")
            fail += 1

        time.sleep(0.3)  # не спамим API

    print(f"\nДонага: {ok} скачано, {skip} уже было, {fail} не удалось")
    print(f"Папка: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
