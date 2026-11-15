"""评测适配器：把异步 Agent 流水线包装成 eval_runner.py 约定的 callable(text) -> dict。

使用独立的 data/eval.db 与 logs/eval.jsonl，避免污染演示数据库；
mock 上游是确定性的，因此不需要多次采样取均值（repeat 仅用于稳定性回归）。
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from server.config import settings  # noqa: E402

settings.DB_PATH = str(ROOT / "data" / "eval.db")
settings.LOG_PATH = str(ROOT / "logs" / "eval.jsonl")

from server import db  # noqa: E402
from server.agent import run_chat  # noqa: E402
from server.models import ChatRequest  # noqa: E402

db.init_db()

_STEPS = re.compile(r"可执行步骤：(.+)$", re.S)


def run_eval_agent(text: str) -> dict:
    """签名约定：单一字符串输入；返回可被 eval_runner.judge 判定的 dict。"""
    try:
        req = ChatRequest(user_id="eval_runner", message=text)
    except Exception as exc:
        return {"answer": f"输入校验失败：{exc}", "error_code": "UNSAFE_INPUT",
                "intent": "refusal", "sources": [], "steps": [], "latency_ms": 0,
                "ttft_ms": 0, "token_usage": 0}
    try:
        res = asyncio.run(run_chat(req))
    except Exception as exc:  # 评测脚本不应因单条异常中断
        return {"answer": f"Agent 异常：{type(exc).__name__}: {exc}",
                "error_code": "UPSTREAM_ERROR", "intent": "", "sources": [],
                "steps": [], "latency_ms": 0, "ttft_ms": 0, "token_usage": 0}
    match = _STEPS.search(res.answer)
    steps = [s.strip() for s in match.group(1).split("；") if s.strip()] if match else []
    return {
        "answer": res.answer,
        "intent": res.intent,
        "error_code": res.error_code,
        "sources": [s.doc_id for s in res.sources],
        "steps": steps,
        "latency_ms": res.latency_ms,
        "ttft_ms": res.ttft_ms,
        "token_usage": res.token_usage,
        "model": res.model,
        "prompt_version": res.prompt_version,
        "request_id": res.request_id,
    }
