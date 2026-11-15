"""极简进程内 TTL 缓存。

用途：/overview、/models 这类只读聚合端点每次请求都要跨多表查询，
实测 120~140ms，而前端每 6 秒轮询一次会放大 DB 压力。给它们加 3~5 秒
的短 TTL，命中后降到毫秒级；任何写操作（改策略 / 停启模型 / 新审计）
显式 invalidate，保证控制台改完立刻可见。

单实例部署足够；多副本时请按改进路线 P0-2 换 Redis。
"""
from __future__ import annotations

import threading
import time
from functools import wraps
from typing import Any, Callable

_LOCK = threading.RLock()
_STORE: dict[str, tuple[float, Any]] = {}


def get(key: str) -> Any:
    with _LOCK:
        item = _STORE.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at < time.monotonic():
            _STORE.pop(key, None)
            return None
        return value


def put(key: str, value: Any, ttl: float) -> None:
    with _LOCK:
        _STORE[key] = (time.monotonic() + ttl, value)


def invalidate(*keys: str) -> None:
    with _LOCK:
        if not keys:
            _STORE.clear()
            return
        for k in keys:
            _STORE.pop(k, None)


def ttl_cache(key: str, seconds: float) -> Callable:
    """同步函数结果缓存装饰器。注意：仅用于无参数的只读聚合函数。"""
    def deco(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            cached = get(key)
            if cached is not None:
                return cached
            value = fn(*args, **kwargs)
            put(key, value, seconds)
            return value
        return wrapper
    return deco


def stats() -> dict[str, Any]:
    with _LOCK:
        now = time.monotonic()
        return {
            "entries": len(_STORE),
            "keys": sorted(_STORE),
            "expired": sum(1 for exp, _ in _STORE.values() if exp < now),
        }
