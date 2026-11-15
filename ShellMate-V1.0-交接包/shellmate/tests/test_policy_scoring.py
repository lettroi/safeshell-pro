"""候选链评分 / 网关令牌 / 停用过滤的客户端测试。"""
from __future__ import annotations

from shellmate.policy import TeamPolicy, candidate_models


def test_disabled_model_is_filtered():
    p = TeamPolicy(disabled_models=["glm-4.7-flash"])
    chain = ["glm-4.7-flash", "glm-4-flash", "Qwen/Qwen3-8B"]
    assert candidate_models(chain, p) == ["glm-4-flash", "Qwen/Qwen3-8B"]


def test_slow_model_moved_to_tail_by_real_stats():
    p = TeamPolicy(model_stats={
        "glm-4-flash": {"avg_latency_ms": 600.0, "success_rate": 1.0, "calls": 20},
        "Qwen/Qwen3-8B": {"avg_latency_ms": 21000.0, "success_rate": 0.4, "calls": 12},
        "Qwen/Qwen2.5-7B-Instruct": {"avg_latency_ms": 400.0, "success_rate": 1.0, "calls": 9},
    })
    chain = ["Qwen/Qwen3-8B", "glm-4-flash", "Qwen/Qwen2.5-7B-Instruct"]
    assert candidate_models(chain, p) == [
        "glm-4-flash", "Qwen/Qwen2.5-7B-Instruct", "Qwen/Qwen3-8B"]
    # 关闭评分时保持管理员原始顺序（稳定）
    assert candidate_models(chain, p, score=False) == chain


def test_no_stats_keeps_original_order():
    p = TeamPolicy()
    chain = ["glm-4-flash", "Qwen/Qwen3-8B"]
    assert candidate_models(chain, p) == chain


def test_whitelist_only_tightens():
    p = TeamPolicy(allowed_models=["glm-4-flash"], disabled_models=["Qwen/Qwen3-8B"])
    assert candidate_models(["glm-4-flash", "Qwen/Qwen3-8B", "X"], p) == ["glm-4-flash"]


def test_default_chain_excludes_disabled_glm47():
    from shellmate.config import load_config
    cfg = load_config()
    assert "glm-4.7-flash" not in cfg.cloud_models_deep
    assert "glm-4.7-flash" not in cfg.cloud_models_light
    assert cfg.cloud_model_deep == "glm-4-flash"


def test_cloud_token_from_env(monkeypatch):
    from shellmate.config import load_config
    monkeypatch.setenv("SHELLMATE_CLOUD_TOKEN", "team-secret")
    assert load_config().cloud_token == "team-secret"
