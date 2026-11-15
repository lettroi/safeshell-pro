"""SafeShell Pro：多模型路由与「政策变化不再免费 → 停用模型」能力测试。

不依赖真实上游网络：raw 端点测试通过 patch resolve_credential 提供假凭据，
在真正发起 HTTP 之前就会命中 model_status 停用检查。
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import connect, db, rate_limit
from server.main import app

client = TestClient(app)

TARGET_MODELS = [
    "glm-4-flash", "glm-4.7-flash",
    "Qwen/Qwen3-8B", "Qwen/Qwen2.5-7B-Instruct",
    "THUDM/GLM-4-9B-0414", "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B",
]


@pytest.fixture(autouse=True)
def _init_and_clean():
    db.init_db()
    yield
    db.execute("DELETE FROM raw_audit WHERE client_id LIKE 'test-%'")
    # 清理本测试用的独立停用/熔断记录，避免污染开发库
    db.execute("DELETE FROM model_status WHERE model LIKE 'test-%'")
    db.execute("DELETE FROM model_health WHERE model LIKE 'test-%'")


def test_all_model_entries_dedup_and_contains_targets():
    """清单按模型名去重（智谱双档在两个预设里都出现，只能有一行），且含 6 个目标模型。"""
    entries = connect.all_model_entries()
    names = [e["model"] for e in entries]
    assert len(names) == len(set(names)), "模型清单出现重复行"
    for m in TARGET_MODELS:
        assert m in names, f"缺少模型 {m}"
    for e in entries:
        # enabled 由 /api/v1/models 端点结合 model_status 补齐，此处只校验清单自身字段
        assert {"model", "provider_id", "name", "region", "configured", "api_base"} <= set(e.keys())


def test_resolve_credential_routes_each_model_to_its_platform():
    """按模型名解析凭据：GLM 两档 → 智谱 base，Qwen/GLM-9B/R1 → 硅基流动 base。"""
    fake = [
        {"provider_id": "zhipu-flash", "api_base": "https://open.bigmodel.cn/api/paas/v4",
         "model": "glm-4-flash", "api_key": "fake", "updated_at": "t"},
        {"provider_id": "siliconflow", "api_base": "https://api.siliconflow.cn/v1",
         "model": "Qwen/Qwen3-8B", "api_key": "fake", "updated_at": "t"},
    ]
    with patch.object(connect, "configured_providers", return_value=fake):
        for m in ("glm-4-flash", "glm-4.7-flash"):
            cred, eff, pid = connect.resolve_credential(m)
            assert cred is not None and "bigmodel.cn" in cred["api_base"]
            assert eff == m
        for m in TARGET_MODELS[2:]:
            cred, eff, pid = connect.resolve_credential(m)
            assert cred is not None and "siliconflow.cn" in cred["api_base"]
            assert eff == m


def test_list_models_endpoint():
    resp = client.get("/api/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    names = [d["model"] for d in data]
    for m in TARGET_MODELS:
        assert m in names
    assert all(isinstance(d["enabled"], bool) for d in data)
    assert all(isinstance(d["configured"], bool) for d in data)


def test_disable_and_enable_model():
    m = "test-policy-disabled-model"
    assert db.get_model_status(m) is True  # 未登记默认启用
    r = client.put("/api/v1/models/status",
                   json={"model": m, "enabled": False, "reason": "不再免费"})
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert db.get_model_status(m) is False
    r2 = client.put("/api/v1/models/status", json={"model": m, "enabled": True})
    assert r2.status_code == 200 and r2.json()["enabled"] is True
    assert db.get_model_status(m) is True


def test_disabled_model_is_rejected_by_raw():
    """政策停用后网关硬拒绝（403），客户端据此跳到下一个候选模型。"""
    m = "test-disabled-raw"
    db.set_model_status(m, False, "政策变化：不再免费")
    rate_limit.reset("test-disabled-client")
    fake_cred = {"api_base": "https://example.invalid/v1", "api_key": "fake",
                 "model": m, "provider_id": "x", "updated_at": "t"}
    try:
        with patch("server.raw.connect.resolve_credential", return_value=(fake_cred, m, "x")):
            resp = client.post(
                "/api/v1/raw/completions",
                headers={"X-Client-ID": "test-disabled-client"},
                json={"model": m, "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "MODEL_DISABLED"
    finally:
        db.set_model_status(m, True)
        rate_limit.reset("test-disabled-client")


def test_disable_then_restore_allows_again():
    """停用 → 网关 403；重新启用 → 不再因停用被拒（DEMO 回退 200）。

    全程 patch resolve_credential，既不依赖开发库里真实凭据，也不发起任何外网请求。
    """
    m = "glm-4-flash"
    rate_limit.reset("test-restore-client")
    fake_cred = {"api_base": "https://example.invalid/v1", "api_key": "fake",
                 "model": m, "provider_id": "x", "updated_at": "t"}
    db.set_model_status(m, False, "测试停用")
    try:
        with patch("server.raw.connect.resolve_credential", return_value=(fake_cred, m, "x")):
            blocked = client.post(
                "/api/v1/raw/completions",
                headers={"X-Client-ID": "test-restore-client"},
                json={"model": m, "messages": [{"role": "user", "content": "hi"}]},
            )
        assert blocked.status_code == 403
        assert blocked.json()["detail"]["error_code"] == "MODEL_DISABLED"
    finally:
        db.set_model_status(m, True)
    assert db.get_model_status(m) is True
    # 恢复后：用 DEMO 凭据（None）验证不再被停用逻辑拦截
    rate_limit.reset("test-restore-client")
    with patch("server.raw.connect.resolve_credential", return_value=(None, None, None)):
        ok = client.post(
            "/api/v1/raw/completions",
            headers={"X-Client-ID": "test-restore-client"},
            json={"model": m, "messages": [{"role": "user", "content": "hi"}]},
        )
    assert ok.status_code == 200
    rate_limit.reset("test-restore-client")


def test_cooldown_fast_reject_and_auto_recover():
    """上游失败 → 模型进入冷却 → 网关快速拒绝（不等超时）→ 冷却到期自动恢复。"""
    m = "test-cooldown-model"
    fake_cred = {"api_base": "https://example.invalid/v1", "api_key": "fake",
                 "model": m, "provider_id": "x", "updated_at": "t"}
    assert db.model_in_cooldown(m) is False

    db.mark_model_failure(m, "RAW_TIMEOUT")
    assert db.model_in_cooldown(m) is True
    health = client.get("/api/v1/models").json()
    assert "cooldown_remaining" in health[0]  # 新字段已随清单返回

    rate_limit.reset("test-cooldown-client")
    with patch("server.raw.connect.resolve_credential", return_value=(fake_cred, m, "x")):
        resp = client.post(
            "/api/v1/raw/completions",
            headers={"X-Client-ID": "test-cooldown-client"},
            json={"model": m, "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 429
    assert resp.json()["detail"]["error_code"] == "MODEL_COOLDOWN"

    # 成功一次（或人工重新启用）即清除冷却
    db.mark_model_success(m)
    assert db.model_in_cooldown(m) is False
    rate_limit.reset("test-cooldown-client")


def test_manual_enable_clears_cooldown():
    m = "test-cooldown-clear"
    db.mark_model_failure(m, "RAW_HTTP_429")
    assert db.model_in_cooldown(m) is True
    db.set_model_status(m, True)
    assert db.model_in_cooldown(m) is False
