"""结构化日志：每行一个 JSON，字段按 references/05 第 5.2 节。

request_id / prompt_version / model / temperature / max_tokens / latency_ms /
ttft_ms / token_usage / retrieval_hits / sources / error_code / intent 均记录，
并对 prompt、answer、user input 做密钥/邮箱/手机号脱敏。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ROOT, settings

_LOCK = threading.Lock()

_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{16,}|Bearer\s+[A-Za-z0-9_\-\.]{16,})")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def redact(text: Any) -> str:
    s = str(text or "")
    s = _SECRET.sub("[REDACTED_KEY]", s)
    s = _EMAIL.sub("[REDACTED_EMAIL]", s)
    s = _PHONE.sub("[REDACTED_PHONE]", s)
    return s


def hash_user(user_id: str) -> str:
    return "u_" + hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]


def _log_path() -> Path:
    p = Path(settings.LOG_PATH)
    if not p.is_absolute():
        p = ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def write_json(payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with _LOCK:
        with _log_path().open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def log_request(row: dict[str, Any]) -> None:
    payload = {
        "type": "request",
        "timestamp": row.get("ts") or datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "request_id": row["request_id"],
        "user_id": hash_user(row.get("user_id", "anonymous")),
        "intent": row.get("intent"),
        "prompt_version": row["prompt_version"],
        "model": row["model"],
        "temperature": row["temperature"],
        "max_tokens": row["max_tokens"],
        "timeout_ms": row["timeout_ms"],
        "retrieval_hits": row.get("retrieval_hits", 0),
        "sources": row.get("sources", []),
        "latency_ms": row["latency_ms"],
        "ttft_ms": row["ttft_ms"],
        "token_usage": row["token_usage"],
        "cost_cny": 0.0,
        "error_code": row.get("error_code"),
        "answer": redact(row.get("answer_digest")),
        "fallback_from": row.get("fallback_from"),
        "routed_by": row.get("routed_by"),
    }
    try:
        write_json(payload)
    except OSError:
        # 日志写失败不能阻塞主响应
        pass


def log_event(level: str, kind: str, message: str, **fields: Any) -> None:
    payload = {
        "type": "event",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "level": level,
        "kind": kind,
        "message": redact(message),
        **{k: (redact(v) if isinstance(v, str) else v) for k, v in fields.items()},
    }
    try:
        write_json(payload)
    except OSError:
        pass
