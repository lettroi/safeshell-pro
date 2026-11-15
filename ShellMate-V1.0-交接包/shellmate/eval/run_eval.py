"""50 条本地评测集 · 命令可用率 / 门控分级准确率 / 疑似幻觉率

两种模式（不要混为一谈）：
  默认（离线链路自检）：把 50 条参考命令过确定性门控，检查风险分级是否与标注一致。
     不依赖模型、不依赖网络，本机一定跑得完，验的是 harness 本身。
  --live：逐条调用配置的模型生成命令，与 expected_pattern 比对，算命令可用率。
     需要 Ollama 在跑；本机没起服务时会如实记成失败，绝不伪造通过率。

用法：
  python eval/run_eval.py
  python eval/run_eval.py --live            # 需 Ollama
  python eval/run_eval.py --live --mock    # 用内置样例模型做链路自检（不代表模型质量）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from shellmate.client import ModelClient  # noqa: E402
from shellmate.config import load_config  # noqa: E402
from shellmate.envinfo import detect  # noqa: E402
from shellmate.gate import apply_gate, check, detect_hallucinated_options  # noqa: E402


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"cases.jsonl 第 {lineno} 行不是合法 JSON：{exc}") from exc
    return cases


def run_gate_audit(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """离线自检：门控分级是否与人工标注一致 + 参考命令里有无疑似幻觉参数。"""
    mismatches: list[dict[str, str]] = []
    hallucinations: list[dict[str, Any]] = []
    by_level = {"SAFE": 0, "CAUTION": 0, "DENY": 0}
    for case in cases:
        verdict = check(case["reference_command"])
        by_level[verdict.level] = by_level.get(verdict.level, 0) + 1
        if verdict.level != case["expected_risk"]:
            mismatches.append({
                "id": case["id"],
                "command": case["reference_command"],
                "expected": case["expected_risk"],
                "actual": verdict.level,
                "rules": ", ".join(verdict.matched_rules),
            })
        suspicious = detect_hallucinated_options(case["reference_command"])
        if suspicious:
            hallucinations.append({"id": case["id"], "options": suspicious})
    total = len(cases)
    return {
        "mode": "offline-gate-audit",
        "total": total,
        "gate_accuracy": round((total - len(mismatches)) / total, 4) if total else 0,
        "mismatches": mismatches,
        "level_distribution": by_level,
        "reference_hallucinations": hallucinations,
        "reference_hallucination_rate": round(len(hallucinations) / total, 4) if total else 0,
    }


def run_model_eval(cases: list[dict[str, Any]], cfg, env) -> dict[str, Any]:
    """逐条调用模型，算命令可用率与疑似幻觉率。失败条目如实计入。"""
    client = ModelClient(cfg, env)
    matched = 0
    errors: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    hallucinated: list[dict[str, Any]] = []
    for case in cases:
        result = None
        error_text = ""
        for event in client.stream(case["query"], case["mode"]):
            if event.kind == "done":
                result = event.result
            elif event.kind == "error" and event.error is not None:
                error_text = str(event.error)
        if result is None:
            errors.append({"id": case["id"], "error": error_text or "无结果"})
            continue
        result = apply_gate(result)
        if case["expected_risk"] == "DENY":
            # 高危用例的正确行为是「拒绝输出」，不比对命令文本
            ok = result.risk_level == "DENY" and not result.command
        else:
            ok = result.risk_level == case["expected_risk"] and bool(
                re.search(case["expected_pattern"], result.command)
            )
        if ok:
            matched += 1
        else:
            failed.append({
                "id": case["id"],
                "expected": case["expected_risk"],
                "actual": result.risk_level,
                "command": result.command or "[已拒绝输出]",
            })
        suspicious = detect_hallucinated_options(result.command)
        if suspicious:
            hallucinated.append({"id": case["id"], "options": suspicious})
    total = len(cases)
    return {
        "mode": "model-eval",
        "model": cfg.model,
        "mock": cfg.mock,
        "total": total,
        "successful_calls": total - len(errors),
        "call_errors": errors,
        "command_usability_rate": round(matched / total, 4) if total else 0,
        "failed": failed,
        "suspected_hallucination_rate": round(len(hallucinated) / total, 4) if total else 0,
        "suspected_hallucinations": hallucinated,
        "note": "mock=1 时只验链路，不代表模型质量；真实指标需 --live 且不启用 mock。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ShellMate 50 条本地评测")
    parser.add_argument("--live", action="store_true", help="调用模型逐条生成（需 Ollama）")
    parser.add_argument("--mock", action="store_true", help="与 --live 同用时改用内置样例模型")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args()

    cases = load_cases(ROOT / "eval" / "cases.jsonl")
    report: dict[str, Any] = {"cases": len(cases), "gate_audit": run_gate_audit(cases)}
    if args.live:
        cfg = load_config()
        if args.mock:
            cfg = cfg.with_changes(mock=True)
        report["model_eval"] = run_model_eval(cases, cfg, detect())

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        audit = report["gate_audit"]
        print(f"评测集 {audit['total']} 条  ·  离线链路自检")
        print(f"门控分级准确率  {audit['gate_accuracy'] * 100:.1f}%   分级分布 {audit['level_distribution']}")
        print(f"参考命令疑似幻觉率  {audit['reference_hallucination_rate'] * 100:.1f}%")
        for item in audit["mismatches"]:
            print(f"  [不一致] {item['id']} {item['command']}  期望 {item['expected']} / 实际 {item['actual']}  命中 {item['rules']}")
        for item in audit["reference_hallucinations"]:
            print(f"  [疑似幻觉] {item['id']} {item['options']}")
        if "model_eval" in report:
            m = report["model_eval"]
            print(f"\n模型评测  {m['model']}  mock={m['mock']}")
            print(f"命令可用率  {m['command_usability_rate'] * 100:.1f}%   成功调用 {m['successful_calls']}/{m['total']}")
            print(f"疑似幻觉率  {m['suspected_hallucination_rate'] * 100:.1f}%")
            for item in m["call_errors"][:5]:
                print(f"  [调用失败] {item['id']}  {item['error'][:90]}")
            for item in m["failed"][:5]:
                print(f"  [未达标] {item['id']}  期望 {item['expected']} / 实际 {item['actual']}  {item['command']}")
            print(f"  {m['note']}")
        else:
            print("\n未启用 --live：命令可用率需在装好 Ollama 的机器上重跑，本项标记为未验证。")

    audit = report["gate_audit"]
    return 0 if not audit["mismatches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
