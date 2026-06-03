from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from PIL import Image, ImageDraw, ImageFont

_W, _H = 800, 600
_PAD = 44

_BG_TOP = (26, 58, 40)
_BG_BOT = (10, 24, 16)
_WHITE = (255, 255, 255)
_WHITE_DIM = (185, 215, 195)
_GREEN_ACCENT = (68, 168, 105)
_DOT_COLOR = (36, 72, 50)

_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

_MONTHS_RU = ["янв", "фев", "мар", "апр", "май", "июн",
               "июл", "авг", "сен", "окт", "ноя", "дек"]


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _gradient(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    tr, tg, tb = _BG_TOP
    br, bg, bb = _BG_BOT
    for y in range(h):
        t = y / h
        r = int(tr + (br - tr) * t)
        g = int(tg + (bg - tg) * t)
        b = int(tb + (bb - tb) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    return img


def _photo_background(photo_bytes: bytes) -> Image.Image:
    """Загружает фото, обрезает под 800×600, накладывает градиентный оверлей.

    Оверлей: прозрачный сверху (фото видно), плотный снизу (текст читаем).
    """
    photo = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    pw, ph = photo.size

    # Scale to cover full 800×600
    scale = max(_W / pw, _H / ph)
    new_w = int(pw * scale)
    new_h = int(ph * scale)
    photo = photo.resize((new_w, new_h), Image.LANCZOS)

    # Center crop
    left = (new_w - _W) // 2
    top = (new_h - _H) // 2
    photo = photo.crop((left, top, left + _W, top + _H))

    # Gradient dark overlay: light at top, heavy at bottom
    overlay = Image.new("RGBA", (_W, _H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for y in range(_H):
        t = y / _H
        # top: ~25% black, bottom: ~82% black
        alpha = int((0.25 + 0.57 * t) * 255)
        draw.line([(0, y), (_W, y)], fill=(0, 0, 0, alpha))

    base = photo.convert("RGBA")
    merged = Image.alpha_composite(base, overlay)
    return merged.convert("RGB")


def _dot_grid(draw: ImageDraw.ImageDraw, color: tuple) -> None:
    for row in range(10):
        for col in range(10):
            cx = _W - _PAD - col * 22
            cy = _PAD + row * 22
            draw.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=color)


def _badge(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font: Any) -> None:
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    px, py = 12, 6
    draw.rounded_rectangle([x, y, x + tw + px * 2, y + th + py * 2], radius=6, fill=_WHITE)
    draw.text((x + px - bbox[0], y + py - bbox[1]), text, font=font, fill=_BG_TOP)


def _wrap(text: str, font: Any, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = font.getbbox(candidate)
        if bbox[2] - bbox[0] <= max_w:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fmt_date(value: Any) -> str:
    if isinstance(value, datetime):
        return f"{value.day} {_MONTHS_RU[value.month - 1]} {value.year}"
    if isinstance(value, str) and value:
        try:
            d = datetime.fromisoformat(value[:19])
            return f"{d.day} {_MONTHS_RU[d.month - 1]} {d.year}"
        except ValueError:
            return value[:10]
    return ""


def generate_card_png(
    *,
    title: str,
    source: str | None = None,
    date: Any = None,
    topic: str | None = None,
    photo_bytes: bytes | None = None,
) -> bytes:
    """Генерирует фоновое PNG 800×600 — только визуальная часть, без текста.

    Текст (заголовок, мета, тег темы) рисуется HTML-оверлеем во фронтенде.
    Если передан photo_bytes — фото с градиентным оверлеем.
    Иначе — зелёный градиент (fallback).
    """
    if photo_bytes:
        img = _photo_background(photo_bytes)
        dot_color = (255, 255, 255, 18)  # едва заметные точки на фото
    else:
        img = _gradient(_W, _H)
        dot_color = _DOT_COLOR

    draw = ImageDraw.Draw(img)

    # Декоративные точки
    _dot_grid(draw, dot_color)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
