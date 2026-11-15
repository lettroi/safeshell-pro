"""S4 性能补测：真实 HTTP + SSE 客户端口径的 TTFT，以及自动切换附加耗时。

PRD 第 6 节定义：
- TTFT = 请求发出 → 首个 SSE `stage` 事件到达（≥20 样本），不是响应体里的自估字段。
- 自动切换附加耗时 = `switch_to()` 前后服务端时间戳差（< 500 ms，p95）。
- 端到端总延迟 = 请求发出 → `respond` 完成，与 TTFT 分开测量。

脚本自启独立 uvicorn 子进程，使用临时 SQLite / 日志目录，不污染演示数据库。
输出：docs/sse_metrics.json
运行：python -m tests.measure_sse
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "sse_metrics.json"
SAMPLES = 24
SWITCH_SAMPLES = 20


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (pos - lo), 1)


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


def answer_messages() -> list[str]:
    lines = (ROOT / "tests" / "eval_dataset.jsonl").read_text(encoding="utf-8").splitlines()
    cases = [json.loads(x) for x in lines if x.strip()]
    picked = [c["input"] for c in cases if c["expected_type"] == "answer"]
    return [picked[i % len(picked)] for i in range(SAMPLES)]


async def measure_one(client: httpx.AsyncClient, base: str, rid: str,
                      message: str) -> tuple[float, float]:
    """返回 (ttft_ms, e2e_http_ms)。先订阅 SSE 并等 connected，再发请求。"""
    ready = asyncio.Event()
    done = asyncio.Event()
    state: dict[str, float] = {"t0": 0.0, "ttft": -1.0}

    async def listen() -> None:
        async with client.stream("GET", f"{base}/api/v1/events/stream?request_id={rid}",
                                 timeout=httpx.Timeout(10.0, read=8.0)) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = json.loads(line[6:])
                if payload.get("status") == "connected":
                    ready.set()
                elif (payload.get("event") == "stage"
                      and payload.get("request_id") == rid
                      and state["t0"] > 0):
                    state["ttft"] = (time.perf_counter() - state["t0"]) * 1000
                    done.set()
                    return

    task = asyncio.create_task(listen())
    try:
        await asyncio.wait_for(ready.wait(), timeout=5.0)
        state["t0"] = time.perf_counter()
        resp = await client.post(f"{base}/api/v1/chat", json={
            "user_id": "perf_probe", "message": message, "request_id": rid,
        })
        e2e = (time.perf_counter() - state["t0"]) * 1000
        if resp.status_code != 200:
            raise RuntimeError(f"chat 返回 {resp.status_code}: {resp.text[:120]}")
        await asyncio.wait_for(done.wait(), timeout=6.0)
        return round(state["ttft"], 1), round(e2e, 1)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, httpx.HTTPError):
            pass


async def collect(base: str) -> tuple[list[float], list[float]]:
    ttfts: list[float] = []
    e2es: list[float] = []
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
        for i, msg in enumerate(answer_messages()):
            rid = f"perf-sse-{i:03d}"
            ttft, e2e = await measure_one(client, base, rid, msg)
            ttfts.append(ttft)
            e2es.append(e2e)
    return ttfts, e2es


def measure_switch(tmp_db: str, tmp_log: str) -> list[float]:
    """服务子进程退出后再测，避免与轮询/请求争锁。"""
    os.environ["DB_PATH"] = tmp_db
    os.environ["LOG_PATH"] = tmp_log
    from server import db, router_engine as engine  # noqa: E402  (env 必须先设置)

    db.init_db()
    durations: list[float] = []
    for i in range(SWITCH_SAMPLES):
        target = "qwen" if i % 2 == 0 else "deepseek"
        t0 = time.perf_counter()
        engine.switch_to(target, "manual")
        durations.append(round((time.perf_counter() - t0) * 1000, 2))
    return durations


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="margin-perf-") as tmp:
        tmp_db = str(Path(tmp) / "perf.db")
        tmp_log = str(Path(tmp) / "perf.jsonl")
        env = os.environ.copy()
        env.update({
            "DB_PATH": tmp_db, "LOG_PATH": tmp_log,
            "POLL_INTERVAL_S": "3600", "APP_HOST": "127.0.0.1",
            "PYTHONUTF8": "1",
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
                raise RuntimeError(f"服务未就绪；uvicorn 输出：\n{out[-2000:]}")
            ttfts, e2es = asyncio.run(collect(base))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

        switch_ms = measure_switch(tmp_db, tmp_log)

    payload = {
        "version": "eval_dataset_v1.1",
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "method": "独立 uvicorn 子进程 + httpx SSE 客户端；TTFT 从 POST 发出计时到首个 stage 帧",
        "environment": {"os": sys.platform, "python": sys.version.split()[0],
                        "provider_mode": "mock", "transport": "127.0.0.1 loopback TCP"},
        "sample_size": len(ttfts),
        "sse_ttft_ms": {"p50": percentile(ttfts, 0.5), "p95": percentile(ttfts, 0.95),
                        "avg": round(statistics.mean(ttfts), 1),
                        "min": min(ttfts), "max": max(ttfts)},
        "e2e_http_ms": {"p50": percentile(e2es, 0.5), "p95": percentile(e2es, 0.95),
                        "avg": round(statistics.mean(e2es), 1)},
        "switch_latency_ms": {"samples": len(switch_ms),
                              "p50": percentile(switch_ms, 0.5),
                              "p95": percentile(switch_ms, 0.95),
                              "avg": round(statistics.mean(switch_ms), 2),
                              "max": max(switch_ms)},
        "raw_ttft_ms": ttfts,
        "measurement_command": "python -m tests.measure_sse",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "raw_ttft_ms"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
