"""raw 透传端点：DEMO 模式下（无真实接入）返回内置行协议 mock；messages 缺失返回 422。

真实上游转发路径（active_provider 有凭据时）由 test_live_upstream 类用例覆盖，本文件只验证
路由契约与 DEMO 回退行为，不依赖数据库连接。
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient
from server import raw as raw_mod
from server.main import app

client = TestClient(app)


@patch.object(raw_mod, "connect")
def test_raw_demo_returns_line_protocol(mock_connect):
    # 无任何已配置凭据 → resolve_credential 返回 (None, None, None) → DEMO mock
    mock_connect.resolve_credential.return_value = (None, None, None)
    resp = client.post(
        "/api/v1/raw/completions",
        json={"messages": [{"role": "user", "content": "怎么查看大于 100M 的文件？"}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["demo"] is True
    content = data["choices"][0]["message"]["content"]
    assert "EXPLAIN:" in content and "COMMAND:" in content


def test_raw_requires_messages():
    resp = client.post("/api/v1/raw/completions", json={"model": "x"})
    assert resp.status_code == 422
