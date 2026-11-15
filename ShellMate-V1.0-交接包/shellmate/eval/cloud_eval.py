"""云端真实模型质量评测（P1-2）：建立「质量 × 延迟」度量，而不是凭印象排候选链。

与 run_eval.py 的区别：
- run_eval.py 默认只做离线门控分级自检；--live 走 Ollama 本机模型。
- 本脚本直接打 SafeShell Pro 网关 /api/v1/raw/completions，按【模型】逐条调用，
  统计行协议合规率、命令可用率、风险分级一致率、延迟分位数与失败原因。

用法：
  python eval/cloud_eval.py --endpoint http://127.0.0.1:8772/api/v1/raw/completions
  python eval/cloud_eval.py --limit 8 --models glm-4-flash,Qwen/Qwen3-8B
  python eval/cloud_eval.py --limit 50 --workers 3 --out eval/baseline_cloud.json

说明：只使用标准库；真实调用会消耗厂商免费额度，默认 limit 较小。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from shellmate.client import parse_line_protocol  # noqa: E402
from shellmate.envinfo import detect  # noqa: E402
from shellmate.gate import apply_gate  # noqa: E402
from shellmate.prompt import build_system_prompt, build_user_prompt  # noqa: E402

DEFAULT_MODELS = [
    "glm-4-flash",
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen3-8B",
    "THUDM/GLM-4-9B-0414",
    "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B",
]


def load_cases(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def one_call(endpoint: str, model: str, case: dict[str, Any], env: Any,
             timeout: float, token: str, client_id: str) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": build_system_prompt(env, "lines")},
        {"role": "user", "content": build_user_prompt(case["query"], case["mode"], None)},
    ]
    payload = {"model": model, "messages": messages, "stream": False,
               "temperature": 0.1, "max_tokens": 320}
    headers = {"Content-Type": "application/json", "X-Client-ID": client_id,
               "X-ShellMate-Mode": case["mode"].lower()}
    if token:
        headers["X-API-Token"] = token
    req = urllib.request.Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers=headers, method="POST")
    t0 = time.perf_counter()
    rec: dict[str, Any] = {"id": case["id"], "model": model, "expected_risk": case["expected_risk"]}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rec["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        content = data["choices"][0]["message"]["content"]
        rec["http_ok"] = True
        try:
            result = parse_line_protocol(content)
            rec["parse_ok"] = True
        except Exception as exc:  # noqa: BLE001 - 解析失败是本评测要度量的指标
            rec["parse_ok"] = False
            rec["error"] = f"PARSE_ERROR: {type(exc).__name__}"
            rec["usable"] = False
            return rec
        result = apply_gate(result)
        rec["actual_risk"] = result.risk_level
        rec["command"] = result.command or ""
        rec["risk_ok"] = result.risk_level == case["expected_risk"]
        if case["expected_risk"] == "DENY":
            rec["usable"] = bool(rec["risk_ok"] and not result.command)
        else:
            rec["usable"] = bool(rec["risk_ok"] and result.command and
                                 re.search(case["expected_pattern"], result.command))
        return rec
    except urllib.error.HTTPError as exc:
        rec["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        rec["http_ok"] = False
        rec["parse_ok"] = False
        rec["usable"] = False
        rec["error"] = f"HTTP_{exc.code}"
        return rec
    except Exception as exc:  # noqa: BLE001
        rec["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        rec["http_ok"] = False
        rec["parse_ok"] = False
        rec["usable"] = False
        rec["error"] = f"{type(exc).__name__}: {exc}"[:120]
        return rec


def summarize(model: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    lat = sorted(r["latency_ms"] for r in records if r.get("latency_ms") is not None)
    errors: dict[str, int] = {}
    for r in records:
        if r.get("error"):
            errors[r["error"]] = errors.get(r["error"], 0) + 1
    return {
        "model": model,
        "cases": total,
        "http_ok": sum(1 for r in records if r.get("http_ok")),
        "parse_ok": sum(1 for r in records if r.get("parse_ok")),
        "parse_success_rate": round(sum(1 for r in records if r.get("parse_ok")) / total, 3) if total else 0,
        "usable": sum(1 for r in records if r.get("usable")),
        "command_usability_rate": round(sum(1 for r in records if r.get("usable")) / total, 3) if total else 0,
        "risk_accuracy": round(sum(1 for r in records if r.get("risk_ok")) / total, 3) if total else 0,
        "avg_latency_ms": round(statistics.mean(lat)) if lat else 0,
        "p50_latency_ms": round(lat[len(lat) // 2]) if lat else 0,
        "p95_latency_ms": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))]) if lat else 0,
        "errors": errors,
        "failed_cases": [r["id"] for r in records if not r.get("usable")][:12],
    }


def main() -> int:
    p = argparse.ArgumentParser(description="SafeShell Pro 云端模型质量评测")
    p.add_argument("--endpoint", default=os.environ.get(
        "SHELLMATE_CLOUD_ENDPOINT", "http://127.0.0.1:8772/api/v1/raw/completions"))
    p.add_argument("--models", default=",".join(DEFAULT_MODELS))
    p.add_argument("--limit", type=int, default=8, help="每个模型评测的用例数（取前 N 条）")
    p.add_argument("--timeout", type=float, default=70.0)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--token", default=os.environ.get("SHELLMATE_CLOUD_TOKEN", ""))
    p.add_argument("--out", default=str(ROOT / "eval" / "baseline_cloud.json"))
    args = p.parse_args()

    cases = load_cases(ROOT / "eval" / "cases.jsonl")[:max(1, args.limit)]
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    env = detect()
    print(f"[eval] {len(models)} models x {len(cases)} cases  endpoint={args.endpoint}", flush=True)

    all_records: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for idx, model in enumerate(models):
        cid = f"cloud-eval-{idx}"
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            recs = list(pool.map(
                lambda c: one_call(args.endpoint, model, c, env, args.timeout, args.token, cid),
                cases))
        s = summarize(model, recs)
        s["wall_seconds"] = round(time.perf_counter() - t0, 1)
        results.append(s)
        all_records.extend(recs)
        print(f"  {model:45s} usable={s['command_usability_rate']*100:5.1f}%  "
              f"parse={s['parse_success_rate']*100:5.1f}%  "
              f"p50={s['p50_latency_ms']:>6}ms  wall={s['wall_seconds']}s", flush=True)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "endpoint": args.endpoint,
        "cases_per_model": len(cases),
        "note": "命令可用率 = 行协议解析成功 + 风险分级正确 + 命令匹配 expected_pattern；"
                "真实调用会消耗免费额度，样本量小仅供排序参考。",
        "by_model": sorted(results, key=lambda x: (-x["command_usability_rate"], x["avg_latency_ms"])),
        "records": all_records,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[eval] baseline written to {out}")
    print(f"[eval] recommended chain: " + ", ".join(
        x["model"] for x in report["by_model"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
