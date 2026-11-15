"""真实接入链路端到端验证：本地假 OpenAI 兼容服务，零外部网络、零真实密钥。

覆盖：测试连接 → 保存并自动启用 → 对话真的走上游（自然语言不再被判 PARSE_ERROR）
      → 移除后回落演示模式。
运行：pytest -q tests/test_live_upstream.py
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

ANSWER_TEXT = "这是来自真实上游的自然语言回答，不是 JSON。"


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        out = json.dumps({
            "choices": [{"message": {"content": ANSWER_TEXT}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 9},
            "model": body.get("model", "fake-model"),
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def fake_base():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from server import db
    from server.config import settings
    from server.main import app

    old_db, old_log = settings.DB_PATH, settings.LOG_PATH
    tmp = tmp_path_factory.mktemp("live")
    settings.DB_PATH = str(tmp / "live.db")
    settings.LOG_PATH = str(tmp / "live.jsonl")
    db.init_db()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        settings.DB_PATH, settings.LOG_PATH = old_db, old_log


def test_full_connect_flow(client: TestClient, fake_base: str):
    cfg = {"provider_id": "zhipu", "api_key": "fake-key-1234567890",
           "api_base": fake_base, "model": "fake-model"}

    # 1. 测试连接：真的发出了一次 /chat/completions
    probe = client.post("/api/v1/connect/test", json=cfg).json()
    assert probe["ok"] is True, probe
    assert ANSWER_TEXT in probe["preview"]

    # 2. 保存并自动启用
    saved = client.post("/api/v1/connect/configure", json=cfg)
    assert saved.status_code == 200
    ov = client.get("/api/v1/overview").json()
    assert ov["demo_mode"] is False and ov["active_provider"] == "zhipu"

    # 3. 普通对话（不带 provider_id）也应走真实上游，且自然语言不报 PARSE_ERROR
    r = client.post("/api/v1/chat", json={
        "user_id": "live_user", "message": "Groq 免费额度是多少",
    }).json()
    assert r["error_code"] is None, r
    assert ANSWER_TEXT in r["answer"], r["answer"]
    assert r["model"] == "fake-model"
    assert r["token_usage"] > 0
    assert r["sources"], "本地政策条目应作为参考材料返回"

    # 4. 显式指定 provider_id 同样走真实上游
    r2 = client.post("/api/v1/chat", json={
        "user_id": "live_user", "message": "Gemini 免费层", "provider_id": "zhipu",
    }).json()
    assert ANSWER_TEXT in r2["answer"]

    # 5. 移除后回到演示模式
    assert client.delete("/api/v1/connect/zhipu").status_code == 200
    assert client.get("/api/v1/overview").json()["active_provider"] is None
    back = client.post("/api/v1/chat", json={
        "user_id": "live_user", "message": "Groq 免费额度是多少",
    }).json()
    assert "依据政策清单" in back["answer"]
    assert ANSWER_TEXT not in back["answer"]
