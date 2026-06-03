from __future__ import annotations

import time
from collections.abc import Hashable
from typing import Any

_CACHE: dict[Hashable, tuple[float, Any]] = {}

# Максимальное количество записей: при превышении вытесняем просроченные,
# а если этого недостаточно — самые старые (LRU-like по времени истечения).
_MAX_CACHE_ENTRIES = 2000
_EVICTION_TARGET = 1500


def _evict() -> None:
    now = time.monotonic()
    expired = [k for k, (exp, _) in _CACHE.items() if exp < now]
    for k in expired:
        _CACHE.pop(k, None)
    if len(_CACHE) >= _MAX_CACHE_ENTRIES:
        surplus = len(_CACHE) - _EVICTION_TARGET
        if surplus > 0:
            oldest = sorted(_CACHE, key=lambda k: _CACHE[k][0])[:surplus]
            for k in oldest:
                _CACHE.pop(k, None)


def cache_get(key: Hashable) -> Any | None:
    item = _CACHE.get(key)
    if not item:
        return None
    expires_at, value = item
    if expires_at < time.monotonic():
        _CACHE.pop(key, None)
        return None
    return value


def cache_set(key: Hashable, value: Any, ttl_seconds: int) -> Any:
    if ttl_seconds <= 0:
        return value
    if len(_CACHE) >= _MAX_CACHE_ENTRIES:
        _evict()
    _CACHE[key] = (time.monotonic() + ttl_seconds, value)
    return value


def cache_delete_prefix(prefix: str) -> None:
    for key in list(_CACHE.keys()):
        if isinstance(key, tuple) and key and key[0] == prefix:
            _CACHE.pop(key, None)
        elif isinstance(key, str) and key.startswith(prefix):
            _CACHE.pop(key, None)


def frozen_list(values: list[Any] | tuple[Any, ...] | None) -> tuple[Any, ...]:
    return tuple(values or ())
