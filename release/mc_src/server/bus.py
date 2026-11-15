"""SSE 事件总线：把后端的真实处理阶段推给前端，禁止前端定时模拟假进度。"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from .config import STAGES


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    def publish(self, payload: dict[str, Any]) -> None:
        if payload.get("stage") and payload["stage"] not in STAGES:
            raise ValueError(f"未登记的后端阶段：{payload['stage']}")
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)

    async def stream(self, request_id: str | None = None) -> AsyncIterator[str]:
        q = self.subscribe()
        try:
            yield "event: connected\ndata: " + json.dumps({"status": "connected"}, ensure_ascii=False) + "\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if request_id and item.get("request_id") not in (None, request_id):
                    continue
                event = item.get("event", "message")
                yield f"event: {event}\ndata: " + json.dumps(item, ensure_ascii=False) + "\n\n"
        finally:
            self.unsubscribe(q)


bus = EventBus()


def emit_stage(request_id: str, stage: str, elapsed_ms: int, detail: str = "") -> None:
    """由 Agent 流水线在真实步骤完成时调用。"""
    bus.publish({
        "event": "stage", "stage": stage, "request_id": request_id,
        "elapsed_ms": elapsed_ms, "detail": detail,
    })


def emit(level: str, kind: str, message: str, **fields: Any) -> None:
    bus.publish({"event": "notification", "level": level, "kind": kind,
                 "message": message, **fields})
