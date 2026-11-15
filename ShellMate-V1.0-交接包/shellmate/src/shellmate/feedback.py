"""本地反馈：只写本机 jsonl，不上传。

这组数据支撑三个 KPI：命令可用率、幻觉率统计口径、风险拦截次数。
界面必须明示存储位置，用户随时可以删掉整个文件。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .client import CommandResult
from .config import Config


def record(
    cfg: Config,
    query: str,
    result: CommandResult,
    mode: str,
    useful: bool | None = None,
) -> Path:
    entry: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": mode,
        "query": query,
        "model": cfg.model,
        "risk_level": result.risk_level,
        "command": result.command,  # DENY 时为空字符串
        "blocked": result.risk_level == "DENY",
        "unverified": result.unverified,
        "matched_rules": result.matched_rules,
        "from_cloud": result.from_cloud,
        "useful": useful,
    }
    path = cfg.feedback_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def stats(cfg: Config) -> dict[str, Any]:
    """读取本机反馈，给出可用率等统计。文件不存在时返回全零结构，不抛错。"""
    path = cfg.feedback_path
    out: dict[str, Any] = {
        "total": 0,
        "useful": 0,
        "not_useful": 0,
        "unrated": 0,
        "blocked": 0,
        "unverified": 0,
        "usefulness_rate": None,
        "path": str(path),
    }
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            out["total"] += 1
            if item.get("blocked"):
                out["blocked"] += 1
            if item.get("unverified"):
                out["unverified"] += 1
            flag = item.get("useful")
            if flag is True:
                out["useful"] += 1
            elif flag is False:
                out["not_useful"] += 1
            else:
                out["unrated"] += 1
    rated = out["useful"] + out["not_useful"]
    out["usefulness_rate"] = round(out["useful"] / rated, 4) if rated else None
    return out
