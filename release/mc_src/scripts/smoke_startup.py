"""S5 干净环境启动冒烟：从空白数据库启动真实 uvicorn 子进程，逐项验证交付面。

验证：健康检查 / Swagger / OpenAPI 示例 / 前端静态托管 / 聚合视图 /
正常问答 / 检索空短路 / 429 切换重放 / SSE 首帧 / 结构化日志脱敏。
使用临时 DB 与日志目录，不碰 data/margin.db。

输出：docs/STARTUP_SMOKE.json；任一检查失败以退出码 1 结束。
运行：python scripts/smoke_startup.py
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "STARTUP_SMOKE.json"

_SECRET = re.compile(r"sk-[A-Za-z0-9_\-]{16,}")
_CHECKS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _CHECKS.append({"name": name, "status": "PASS" if ok else "FAIL", "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_health(base: str, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    with httpx.Client(timeout=1.0, trust_env=False) as c:
        while time.time() < deadline:
            try:
                if c.get(base + "/api/v1/health").status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
    return False


def run_checks(base: str, log_path: str) -> None:
    with httpx.Client(timeout=10.0, trust_env=False) as c:
        r = c.get(base + "/api/v1/health")
        h = r.json() if r.status_code == 200 else {}
        check("health 200 / status=ok / db_ok / poller_alive",
              r.status_code == 200 and h.get("status") == "ok"
              and h.get("db_ok") is True and h.get("poller_alive") is True,
              json.dumps(h, ensure_ascii=False))

        r = c.get(base + "/docs")
        check("Swagger /docs 可访问", r.status_code == 200 and "swagger" in r.text.lower(),
              f"HTTP {r.status_code}")

        r = c.get(base + "/openapi.json")
        schema = r.json() if r.status_code == 200 else {}
        examples = schema.get("components", {}).get("schemas", {}).get("ChatRequest", {}).get("examples")
        check("OpenAPI 中 ChatRequest 带请求示例", bool(examples),
              f"{len(examples) if examples else 0} 个示例")

        if (ROOT / "web" / "dist" / "index.html").is_file():
            r = c.get(base + "/")
            check("前端静态产物由 FastAPI 托管", r.status_code == 200 and "<div id=\"root\"" in r.text,
                  f"HTTP {r.status_code}")
        else:
            _CHECKS.append({"name": "前端静态产物托管", "status": "SKIP", "detail": "web/dist 未构建"})
            print("[SKIP] 前端静态产物托管 — web/dist 未构建")

        r = c.get(base + "/api/v1/overview")
        ov = r.json() if r.status_code == 200 else {}
        check("额度总览聚合视图（9 个数据源 + 策略）",
              len(ov.get("providers", [])) == 9 and bool(ov.get("strategy", {}).get("current_id")),
              f"providers={len(ov.get('providers', []))}")

        r = c.post(base + "/api/v1/chat", json={
            "user_id": "smoke_user",
            "message": "Gemini 2.0 Flash 还有免费额度吗？耗尽后会自动切到哪家？",
            "request_id": "smoke-chat-001",
        })
        body = r.json() if r.status_code == 200 else {}
        check("正常问答：无错误码 / 有来源 / 六阶段 / token<500",
              r.status_code == 200 and not body.get("error_code") and len(body.get("sources", [])) > 0
              and len(body.get("stages", [])) == 6 and 0 < body.get("token_usage", 0) < 500,
              f"error={body.get('error_code')} sources={len(body.get('sources', []))} "
              f"stages={len(body.get('stages', []))} tokens={body.get('token_usage')}")

        r = c.post(base + "/api/v1/chat", json={
            "user_id": "smoke_user", "message": "量子引力最新论文讲了什么",
            "request_id": "smoke-nocontext-001",
        })
        body = r.json() if r.status_code == 200 else {}
        check("检索为空短路 NO_CONTEXT 且不调用模型",
              body.get("error_code") == "NO_CONTEXT" and body.get("sources") == []
              and "generate" not in [s["stage"] for s in body.get("stages", [])],
              f"error={body.get('error_code')}")

        before = c.get(base + "/api/v1/strategy").json().get("current_id")
        drill = c.post(base + "/api/v1/simulate/429").json()
        after = c.get(base + "/api/v1/strategy").json().get("current_id")
        check("429 演练：自动切换 + 同 request_id 重放成功",
              drill.get("replay_status") == 200 and before != after,
              f"{before} → {after}, replay={drill.get('replay_status')}")

        try:
            with c.stream("GET", base + "/api/v1/events/stream", timeout=3.0) as resp:
                head: list[str] = []
                for line in resp.iter_lines():
                    head.append(line)
                    if len(head) >= 3:  # event: connected / data: {...} / 空行
                        break
                joined = "\n".join(head)
                check("SSE 首帧 connected",
                      resp.status_code == 200 and "event: connected" in joined
                      and '"connected"' in joined,
                      joined.replace("\n", " | ")[:80])
        except httpx.HTTPError as exc:
            check("SSE 首帧 connected", False, str(exc)[:120])

        logs = c.get(base + "/api/v1/logs?limit=50").text
        check("日志接口不回显明文密钥", not _SECRET.search(logs))

        presets = c.get(base + "/api/v1/connect/presets").json()
        check("新手接入预设可用（≥3 家、含官方链接与手把手步骤）",
              len(presets) >= 3
              and all(p["key_url"].startswith("https://") and len(p["steps"]) >= 3
                      for p in presets),
              f"{len(presets)} 家")
        probe = c.post(base + "/api/v1/connect/test",
                       json={"provider_id": "zhipu", "api_key": ""}).json()
        check("未填 Key 时给出可操作的错误提示",
              probe["ok"] is False and probe["error_code"] == "INVALID_KEY"
              and bool(probe["hint"]), probe["message"])

    log_text = Path(log_path).read_text(encoding="utf-8") if Path(log_path).is_file() else ""
    check("结构化日志文件不含明文密钥", bool(log_text) and not _SECRET.search(log_text),
          f"{len(log_text.splitlines())} 行")
    check("结构化日志 user_id 为哈希值",
          any('"user_id":"u_' in line for line in log_text.splitlines() if '"type":"request"' in line))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="margin-smoke-") as tmp:
        log_path = str(Path(tmp) / "smoke.jsonl")
        env = os.environ.copy()
        env.update({
            "DB_PATH": str(Path(tmp) / "smoke.db"), "LOG_PATH": log_path,
            "POLL_INTERVAL_S": "3600", "APP_HOST": "127.0.0.1", "PYTHONUTF8": "1",
        })
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "warning"],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        try:
            if not wait_health(base):
                out, _ = proc.communicate(timeout=5)
                check("服务从空白数据库启动", False, f"uvicorn: {out[-800:]}")
            else:
                check("服务从空白数据库启动", True, f"port={port}")
                run_checks(base, log_path)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    failed = [c for c in _CHECKS if c["status"] == "FAIL"]
    payload = {
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "method": "空白临时 SQLite + 独立 uvicorn 子进程 + httpx 真实 HTTP",
        "total": len(_CHECKS), "passed": sum(c["status"] == "PASS" for c in _CHECKS),
        "failed": len(failed), "skipped": sum(c["status"] == "SKIP" for c in _CHECKS),
        "checks": _CHECKS,
        "measurement_command": "python scripts/smoke_startup.py",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n汇总：{payload['passed']} PASS / {payload['failed']} FAIL / "
          f"{payload['skipped']} SKIP → {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
