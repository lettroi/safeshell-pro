"""按客户端的简易令牌桶限流。

SafeShell Pro 是团队产品：网关要防止单个客户端把免费模型额度（如 GLM 免费档 30 RPM）
瞬间打爆，影响同组织其他成员。这里用进程内令牌桶，按 X-Client-ID 维度限流；
无该头时退化为 anonymous 共享桶。

设计取舍：
- 进程内、非分布式：单实例部署（Docker 单容器）足够；多实例横向扩展时应在前置 LB
  做限流或用 Redis。本版本明确为单实例策略，README 标注。
- 失败开放（fail-open）：令牌桶状态异常时放行，避免限流组件自身阻塞主链路。
"""
from __future__ import annotations

import threading
import time

from .config import settings

_lock = threading.RLock()
_buckets: dict[str, dict[str, float]] = {}


def _bucket(client_id: str, rpm: int) -> dict[str, float]:
    now = time.time()
    b = _buckets.get(client_id)
    if b is None:
        b = {"tokens": float(rpm), "last": now, "capacity": float(rpm)}
        _buckets[client_id] = b
        return b
    # 按经过时间补充令牌（最多补到 capacity）
    elapsed = now - b["last"]
    refill = elapsed * (rpm / 60.0)
    if refill > 0:
        b["tokens"] = min(b["capacity"], b["tokens"] + refill)
        b["last"] = now
    return b


def check(client_id: str, rpm: int | None = None) -> bool:
    """返回 True 表示允许本次请求；False 表示已触发限流（应返回 429）。"""
    rpm = rpm or settings.RAW_RPM_PER_CLIENT
    if rpm <= 0:
        return True
    try:
        with _lock:
            b = _bucket(client_id, rpm)
            if b["tokens"] >= 1.0:
                b["tokens"] -= 1.0
                return True
            return False
    except Exception:
        # 失败开放：限流组件异常不应阻塞主链路
        return True


def reset(client_id: str | None = None) -> None:
    """测试 / 运维用：清空或全部重置桶状态。"""
    with _lock:
        if client_id is None:
            _buckets.clear()
        else:
            _buckets.pop(client_id, None)
