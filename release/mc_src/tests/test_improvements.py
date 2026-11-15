"""本轮改进项的回归测试：默认停用模型、审计哈希链、导出、可选鉴权、缓存、指标、分级超时。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from server import cache, db, rate_limit, raw
from server.config import settings
from server.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _init_db():
    db.init_db()
    cache.invalidate()
    yield
    cache.invalidate()
    db.execute("DELETE FROM raw_audit WHERE client_id LIKE 'test-improve%'")


# ---------------------------------------------------------------------------
# 1. 停用 glm-4.7-flash（用户决定；默认停用，可人工重新启用）
# ---------------------------------------------------------------------------
def test_glm47_default_disabled():
    db.execute("DELETE FROM model_status WHERE model = 'glm-4.7-flash'")
    assert db.get_model_status("glm-4.7-flash") is False
    assert "429" in db.model_status_reason("glm-4.7-flash")
    assert db.get_model_status("glm-4-flash") is True
    # 管理员显式重新启用可覆盖默认值
    db.set_model_status("glm-4.7-flash", True, "临时恢复")
    assert db.get_model_status("glm-4.7-flash") is True
    db.execute("DELETE FROM model_status WHERE model = 'glm-4.7-flash'")


def test_disabled_model_rejected_by_raw():
    rate_limit.reset("test-improve-disabled")
    fake_cred = {"api_base": "https://example.invalid/v1", "model": "glm-4.7-flash",
                 "api_key": "not-a-real-key"}
    with patch.object(raw.connect, "resolve_credential",
                      return_value=(fake_cred, "glm-4.7-flash", "zhipu")):
        resp = client.post(
            "/api/v1/raw/completions",
            headers={"X-Client-ID": "test-improve-disabled"},
            json={"model": "glm-4.7-flash", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "MODEL_DISABLED"


def test_models_endpoint_exposes_reason_and_stats():
    resp = client.get("/api/v1/models")
    assert resp.status_code == 200
    row = next(x for x in resp.json() if x["model"] == "glm-4.7-flash")
    assert row["enabled"] is False
    assert row["reason"]
    assert "avg_latency_ms" in row and "success_rate" in row


# ---------------------------------------------------------------------------
# 2. 审计哈希链（使用独立临时库，绝不污染开发 DB）
# ---------------------------------------------------------------------------
@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch):
    path = tmp_path / "audit.db"
    monkeypatch.setattr(db, "db_path", lambda: path)
    db.init_db()
    return path


def _row(i: int) -> dict:
    return {"ts": f"2026-11-15T00:00:{i:02d}Z", "client_id": "test-improve-chain",
            "model": "glm-4-flash", "mode": "nl2cmd", "ok": True,
            "latency_ms": 100 + i, "total_tokens": 10, "request_id": f"r{i}"}


def test_audit_hash_chain_ok_and_tamper_detected(isolated_db):
    for i in range(4):
        db.add_raw_audit(_row(i))
    result = db.verify_audit_chain()
    assert result["ok"] is True
    assert result["checked"] == 4

    rows = db.fetch_all("SELECT id FROM raw_audit ORDER BY id ASC")
    victim = rows[2]["id"]
    original = db.fetch_one("SELECT latency_ms FROM raw_audit WHERE id = ?", (victim,))
    db.execute("UPDATE raw_audit SET latency_ms = ? WHERE id = ?", (99999, victim))
    bad = db.verify_audit_chain()
    assert bad["ok"] is False
    assert bad["first_bad_id"] == victim
    db.execute("UPDATE raw_audit SET latency_ms = ? WHERE id = ?",
               (original["latency_ms"], victim))
    assert db.verify_audit_chain()["ok"] is True


def test_audit_hash_chain_prev_link_detected(isolated_db):
    for i in range(3):
        db.add_raw_audit(_row(i))
    rows = db.fetch_all("SELECT id FROM raw_audit ORDER BY id ASC")
    db.execute("UPDATE raw_audit SET prev_hash = 'deadbeef' WHERE id = ?", (rows[1]["id"],))
    # 重算 digest 时 prev_hash 也参与，所以必然被识别
    assert db.verify_audit_chain()["ok"] is False


def test_audit_verify_endpoint_and_export(isolated_db, monkeypatch):
    # 端点用同一个临时库（db.db_path 已被 fixture 替换）
    db.add_raw_audit(_row(1))
    ok = client.get("/api/v1/audit/verify")
    assert ok.status_code == 200
    assert ok.json()["ok"] is True

    jsonl = client.get("/api/v1/audit/export?format=jsonl")
    assert jsonl.status_code == 200
    assert "row_hash" in json.loads(jsonl.text.strip().splitlines()[0])

    csv_out = client.get("/api/v1/audit/export?format=csv")
    assert csv_out.status_code == 200
    assert "id,ts,client_id" in csv_out.text


def test_audit_fields_extracts_only_hash():
    data = {"choices": [{"message": {"content":
        "EXPLAIN: hi\n---\nCOMMAND: ls -la\nRISK: SAFE\nREASON: read only"}}]}
    risk, cmd_hash = raw.audit_fields(data)
    assert risk == "SAFE"
    assert cmd_hash and len(cmd_hash) == 64
    # 只返回哈希，不把命令原文带进审计写入路径
    assert "ls -la" not in (risk, cmd_hash)
    assert raw.audit_fields({"choices": []}) == (None, None)


# ---------------------------------------------------------------------------
# 3. 可选共享令牌鉴权（默认关闭 = 零配置）
# ---------------------------------------------------------------------------
def test_auth_disabled_by_default():
    assert settings.SAFESHELL_API_TOKEN == ""
    assert client.get("/api/v1/models").status_code == 200


def test_auth_required_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "SAFESHELL_API_TOKEN", "unit-secret")
    assert client.get("/api/v1/models").status_code == 401
    assert client.get("/api/v1/health").status_code == 200  # health 始终开放
    ok = client.get("/api/v1/models", headers={"X-API-Token": "unit-secret"})
    assert ok.status_code == 200
    bearer = client.get("/api/v1/models", headers={"Authorization": "Bearer unit-secret"})
    assert bearer.status_code == 200


# ---------------------------------------------------------------------------
# 4. 缓存 / 指标 / 分级超时
# ---------------------------------------------------------------------------
def test_ttl_cache_and_invalidate():
    calls = {"n": 0}

    @cache.ttl_cache("test-improve-cache", 30)
    def compute() -> int:
        calls["n"] += 1
        return calls["n"]

    assert compute() == 1
    assert compute() == 1
    cache.invalidate("test-improve-cache")
    assert compute() == 2


def test_metrics_endpoint():
    resp = client.get("/api/v1/metrics")
    assert resp.status_code == 200
    assert "safeshell_raw_calls_total" in resp.text
    assert "safeshell_model_success_rate" in resp.text or "safeshell_cache_entries" in resp.text


def test_model_timeout_grading():
    assert raw.model_timeout("glm-4-flash") == 12.0
    assert raw.model_timeout("THUDM/GLM-4-9B-0414") == 50.0
    assert raw.model_timeout("some-unknown-model") == settings.RAW_UPSTREAM_TIMEOUT_S


def test_model_latency_stats_shape():
    stats = db.model_latency_stats(60)
    assert isinstance(stats, dict)
    for s in stats.values():
        assert {"calls", "success_rate", "avg_latency_ms"} <= set(s)
