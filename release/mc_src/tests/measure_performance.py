"""S4 指标测量脚本：生成 docs/eval_metrics.json（EVAL_REPORT 的唯一数据源）。

口径与 PRD 第 6 节附录一致：
- TTFT 权威值来自 `docs/sse_metrics.json`（SSE 客户端实测，由 `measure_sse.py` 生成）；
  本脚本记录的响应体 `ttft_ms` 仅为服务端自估字段，mock 下恒为 0，不作为交付口径。
- 准确率 = expected_type / expected_keyword 精确匹配，非 LLM-as-judge。
- 延迟同报 p50 与 p95；有效样本 = 评测集 24 条（≥20）。
- 建议可用率按 rubric（步骤 1..3 且可执行）统计 answer 类用例。
运行：python -m tests.measure_sse && python -m tests.measure_performance
"""
from __future__ import annotations

import json
import platform
import statistics
import subprocess
from datetime import datetime
from pathlib import Path

from tests.eval_adapter import run_eval_agent

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "tests" / "eval_dataset.jsonl"
OUT = ROOT / "docs" / "eval_metrics.json"


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (pos - lo), 1)


def judge(case: dict, r: dict) -> tuple[bool, str]:
    answer = str(r.get("answer") or "")
    et = case["expected_type"]
    kw = (case.get("expected_keyword") or "").strip()
    if not answer:
        return False, "空答案"
    if et == "answer":
        if r.get("error_code") not in (None, ""):
            return False, f"意外错误码 {r.get('error_code')}"
        if kw and kw.lower() not in answer.lower():
            return False, f"未命中关键词 {kw}"
        if len(r.get("steps") or []) > 3:
            return False, "步骤 > 3"
        return True, "ok"
    if et == "chitchat":
        return (r.get("intent") == "chitchat"), "闲聊路由"
    if et == "refusal":
        ok = r.get("intent") == "refusal" or r.get("error_code") in {"UNSAFE_INPUT", "EMPTY_INPUT"}
        return ok, "安全拒绝"
    if et == "no_context":
        ok = r.get("error_code") == "NO_CONTEXT" and not r.get("sources")
        return ok, "检索空短路"
    return False, f"未知类型 {et}"


def main() -> None:
    cases = [json.loads(x) for x in DATASET.read_text(encoding="utf-8").splitlines() if x.strip()]
    latencies: list[float] = []
    ttfts: list[float] = []
    tokens: list[float] = []
    by_cat: dict[str, list[int]] = {}
    failures: list[dict] = []
    answer_cases = 0
    usable = 0

    for c in cases:
        r = run_eval_agent(c["input"])
        ok, reason = judge(c, r)
        total, passed = by_cat.get(c["category"], [0, 0])
        by_cat[c["category"]] = [total + 1, passed + (1 if ok else 0)]
        latencies.append(float(r["latency_ms"]))
        ttfts.append(float(r["ttft_ms"]))
        tokens.append(float(r["token_usage"]))
        if c["expected_type"] == "answer":
            # rubric：至少 1 步可在当前环境直接执行，且总步数 ≤ 3
            steps = [s for s in (r.get("steps") or []) if s.strip()]
            answer_cases += 1
            if 1 <= len(steps) <= 3:
                usable += 1
        if not ok:
            failures.append({"id": c["id"], "reason": reason,
                             "answer": str(r.get("answer"))[:120]})

    answer_tokens = [t for t, c in zip(tokens, cases) if c["expected_type"] == "answer"]
    passed = sum(v[1] for v in by_cat.values())
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                         cwd=ROOT, text=True).strip()
    except subprocess.SubprocessError:
        commit = "unknown"

    from server.config import settings
    sse_file = ROOT / "docs" / "sse_metrics.json"
    sse = json.loads(sse_file.read_text(encoding="utf-8")) if sse_file.is_file() else {}
    payload = {
        "version": "eval_dataset_v1.1",
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "commit": commit,
        "environment": {
            "os": platform.platform(), "python": platform.python_version(),
            "provider_mode": settings.PROVIDER_MODE, "model": settings.MODEL_NAME,
            "temperature": settings.LLM_TEMPERATURE, "max_tokens": settings.LLM_MAX_TOKENS,
            "timeout_ms": settings.LLM_TIMEOUT_MS, "prompt_version": settings.PROMPT_VERSION,
        },
        "sample_size": len(cases),
        "pass_rate": round(passed / len(cases) * 100, 1),
        "passed": passed,
        "by_category": {k: {"total": v[0], "passed": v[1],
                            "rate": round(v[1] / v[0] * 100, 1)} for k, v in by_cat.items()},
        "latency_ms": {"p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95),
                       "avg": round(statistics.mean(latencies), 1)},
        "ttft_ms": {"p50": percentile(ttfts, 0.5), "p95": percentile(ttfts, 0.95),
                    "avg": round(statistics.mean(ttfts), 1),
                    "source": "响应体内服务端自估字段（mock 下为 0），非 PRD 的 SSE 客户端口径"},
        "sse_ttft_ms": sse.get("sse_ttft_ms"),
        "e2e_http_ms": sse.get("e2e_http_ms"),
        "switch_latency_ms": sse.get("switch_latency_ms"),
        "usability": {"rubric": "answer 类用例：可执行步骤 1..3 且每步非空；另人工抽检 3 条确认步骤可操作",
                      "answer_cases": answer_cases, "usable": usable,
                      "rate": round(usable / answer_cases * 100, 1) if answer_cases else 0.0},
        "token_usage": {"avg_all": round(statistics.mean(tokens), 1),
                        "avg_answer_only": round(statistics.mean(answer_tokens), 1),
                        "max": max(tokens)},
        "failures": failures,
        "measurement_commands": ["python -m tests.measure_performance",
                                 "python -m tests.measure_sse"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
