"""SafeShell Pro 商业能力测试：成本节省估算、按客户端限流、团队策略、raw 审计落库与聚合。

不依赖真实上游网络：raw 端点测试通过 patch connect 强制 DEMO 回退，只验证审计落库与
报告聚合的链路；成本 / 限流 / 策略均为纯函数或本地 DB 测试。
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import cost, rate_limit, db
from server.main import app
from server.config import settings

client = TestClient(app)


@pytest.fixture(autouse=True)
def _init_and_clean():
    # TestClient 不触发 lifespan，需手动确保新表存在；只建表不擦数据
    db.init_db()
    yield
    # 只清掉本测试写入的 client_id，避免污染开发用 margin.db
    db.execute("DELETE FROM raw_audit WHERE client_id LIKE 'test-%'")
    db.execute("DELETE FROM raw_audit WHERE client_id = 'audit-demo-client'")


def test_cost_estimate_free_is_zero():
    e = cost.estimate(1000, 2000)
    assert e["billed"] == 0.0
    # 付费等价 = 1*0.005 + 2*0.015 = 0.035
    assert abs(e["paid_equivalent"] - 0.035) < 1e-9
    assert abs(e["saved"] - 0.035) < 1e-9


def test_rate_limit_enforces_rpm():
    cid = "test-rl-client"
    rate_limit.reset(cid)
    prev = settings.RAW_RPM_PER_CLIENT
    settings.RAW_RPM_PER_CLIENT = 3
    try:
        for _ in range(3):
            assert rate_limit.check(cid) is True
        assert rate_limit.check(cid) is False
    finally:
        settings.RAW_RPM_PER_CLIENT = prev
        rate_limit.reset(cid)


def test_policy_get_set_roundtrip():
    orig = db.get_policy()
    updated = db.set_policy({"risk_threshold": "CAUTION", "max_tokens": 256}, "tester")
    assert updated["risk_threshold"] == "CAUTION"
    assert updated["max_tokens"] == 256
    assert db.get_policy()["risk_threshold"] == "CAUTION"
    # 还原，避免影响其他测试 / 演示数据
    db.set_policy({"risk_threshold": orig["risk_threshold"], "max_tokens": orig["max_tokens"]})


def test_raw_audit_logs_and_report():
    cid = "audit-demo-client"
    with patch("server.raw.connect") as mock_connect:
        mock_connect.resolve_credential.return_value = (None, None, None)
        resp = client.post(
            "/api/v1/raw/completions",
            headers={"X-Client-ID": cid, "X-ShellMate-Mode": "nl2cmd"},
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
    assert resp.status_code == 200
    logs = client.get("/api/v1/audit/logs", params={"client_id": cid, "limit": 10})
    assert logs.status_code == 200
    rows = logs.json()
    assert any(r["client_id"] == cid and r["mode"] == "nl2cmd" for r in rows)

    rep = client.get("/api/v1/audit/report", params={"client_id": cid})
    assert rep.status_code == 200
    body = rep.json()
    assert body["client_id"] == cid
    assert body["total_calls"] >= 1
    assert body["billed_cny"] == 0.0


def test_audit_aggregate_saved_from_tokens():
    cid = "test-aggregate-client"
    db.add_raw_audit({
        "ts": db.now_iso(), "client_id": cid, "model": "glm-4-flash", "mode": "nl2cmd",
        "prompt_tokens": 1000, "completion_tokens": 2000, "total_tokens": 3000,
        "latency_ms": 50, "ok": True, "billed": 0.0, "saved": 0.035, "request_id": "r1",
    })
    rep = db.raw_audit_report(cid)
    assert rep["total_calls"] == 1
    assert rep["total_tokens"] == 3000
    assert abs(rep["estimated_saved_cny"] - 0.035) < 1e-9
    assert rep["by_model"][0]["model"] == "glm-4-flash"


def test_policy_endpoint_rejects_bad_threshold():
    resp = client.put("/api/v1/policy", json={"risk_threshold": "BOGUS"})
    assert resp.status_code == 422
