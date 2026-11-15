"""S4 契约 / 回归 / 故障注入测试。运行：pytest -q"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
DATASET = Path(__file__).parent / "eval_dataset.jsonl"

from server.config import settings  # noqa: E402

settings.DB_PATH = str(ROOT / "data" / "test.db")
settings.LOG_PATH = str(ROOT / "logs" / "test.jsonl")

from server import db  # noqa: E402
from server.main import app  # noqa: E402
from server.models import ChatResponse  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def clean_db():
    p = Path(settings.DB_PATH)
    if p.exists():
        p.unlink()
    db.init_db()
    yield


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def load_cases() -> list[dict]:
    rows = []
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


CASES = load_cases()


def post(client: TestClient, message: str, inject: str | None = None) -> dict:
    body: dict = {"user_id": "pytest_user", "message": message,
                  "request_id": f"pt-{abs(hash(message)) % 10**9}"}
    if inject:
        body["inject"] = inject
    r = client.post("/api/v1/chat", json=body)
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
    return r.json()


def stages_of(result: dict) -> list[str]:
    return [s["stage"] for s in result.get("stages", [])]


# ---------- 1. 评测集与数据集纪律 ----------
def test_dataset_validity():
    assert len(CASES) >= 10
    assert {c["category"] for c in CASES} == {"functional", "boundary", "safety", "robustness"}
    assert {"chitchat", "refusal", "no_context"} <= {c["expected_type"] for c in CASES}
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    for c in CASES:
        assert all(k in c for k in ("id", "input", "expected_type", "category"))
        if c["expected_type"] == "answer":
            assert c.get("expected_keyword"), f"{c['id']} 缺 expected_keyword"


def test_dataset_not_leaked_into_prompt():
    prompt = (ROOT / "server" / "prompts" / f"{settings.PROMPT_VERSION}.txt").read_text(encoding="utf-8")
    leaked = [c["id"] for c in CASES if c["input"] in prompt]
    assert not leaked, f"评测集泄漏到 Prompt：{leaked}"


# ---------- 2. 黄金评测集参数化回归 ----------
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_golden_dataset(client: TestClient, case: dict):
    r = post(client, case["input"])
    answer = r["answer"]
    et = case["expected_type"]
    assert answer, f"{case['id']} 返回空答案"
    if et == "answer":
        assert r["error_code"] in (None, ""), f"{case['id']} 意外错误码 {r['error_code']}"
        assert case["expected_keyword"].lower() in answer.lower(), (
            f"{case['id']} 未命中 {case['expected_keyword']}：{answer[:120]}")
        steps = re.findall(r"\d+\.\s", answer)
        assert len(steps) <= 3, f"{case['id']} 步骤超过 3"
    elif et == "chitchat":
        assert r["intent"] == "chitchat"
        assert "generate" not in stages_of(r), "闲聊不应调用模型"
    elif et == "refusal":
        assert r["intent"] == "refusal" or r["error_code"] in {"UNSAFE_INPUT", "EMPTY_INPUT"}
        assert "generate" not in stages_of(r), "拒绝请求不应调用模型"
    elif et == "no_context":
        assert r["error_code"] == "NO_CONTEXT"
        assert "未找到" in answer
        assert r["sources"] == [], "检索为空不得返回来源"
        assert "generate" not in stages_of(r), "检索为空必须短路"
    else:
        pytest.fail(f"未知 expected_type {et}")
    assert r["latency_ms"] < 5000


# ---------- 3. 契约测试 ----------
def test_chat_response_contract(client: TestClient):
    r = client.post("/api/v1/chat", json={"user_id": "u1", "message": "Gemini 免费额度"})
    assert r.status_code == 200
    assert r.headers.get("X-Request-ID")
    body = r.json()
    for field in ("answer", "sources", "latency_ms", "ttft_ms", "request_id",
                  "prompt_version", "token_usage", "model", "intent", "stages"):
        assert field in body, f"响应缺字段 {field}"
    assert ChatResponse(**body)


@pytest.mark.parametrize("payload", [
    {"user_id": "u1"},
    {"user_id": "u1", "message": ""},
    {"user_id": "bad user!", "message": "hi"},
    {"user_id": "u1", "message": "x" * 5000},
])
def test_invalid_payload_returns_422(client: TestClient, payload: dict):
    assert client.post("/api/v1/chat", json=payload).status_code == 422


def test_swagger_has_request_examples(client: TestClient):
    schema = client.get("/openapi.json").json()
    chat = schema["paths"]["/api/v1/chat"]["post"]
    assert chat["summary"] and chat["description"]
    assert "examples" in schema["components"]["schemas"]["ChatRequest"]


def test_no_plaintext_key_in_responses(client: TestClient):
    for path in ("/api/v1/overview", "/api/v1/providers", "/api/v1/logs",
                 "/api/v1/connect/presets", "/api/v1/connect/status"):
        assert not re.search(r"sk-[A-Za-z0-9_\-]{20,}", client.get(path).text), path


# ---------- 4. 故障注入与兜底 ----------
@pytest.mark.parametrize("inject,code", [
    ("timeout", "LLM_TIMEOUT"),
    ("parse_error", "PARSE_ERROR"),
])
def test_failure_injection_returns_structured_fallback(client: TestClient, inject: str, code: str):
    r = post(client, "Gemini 免费额度与自动切换", inject)
    assert r["error_code"] == code
    assert r["answer"] and "Traceback" not in r["answer"]
    assert stages_of(r)[-1] == "respond"
    assert r["request_id"]


def test_empty_retrieval_short_circuits_llm(client: TestClient):
    r = post(client, "Gemini 免费额度", "no_context")
    stages = stages_of(r)
    assert r["error_code"] == "NO_CONTEXT"
    assert "generate" not in stages
    assert r["sources"] == []


def test_rate_limit_triggers_failover_replay(client: TestClient):
    r = post(client, "Gemini 免费额度", "rate_limit")
    assert r["routed_by"] == "failover_429"
    assert r["fallback_from"]
    assert r["error_code"] in (None, "")
    assert "429" in " ".join(e["message"] for e in client.get("/api/v1/events").json())


def test_429_drill_switches_and_replays(client: TestClient):
    before = client.get("/api/v1/strategy").json()
    r = client.post("/api/v1/simulate/429").json()
    after = client.get("/api/v1/strategy").json()
    assert r["replay_status"] == 200
    assert after["current_id"] != before["current_id"]
    assert after["switch_count"] > before["switch_count"]
    kinds = [e["kind"] for e in r["events"]]
    assert "http_429" in kinds and "replay" in kinds


def test_all_error_codes_have_fallback_copy():
    from server.config import ERROR_CODES
    assert {"LLM_TIMEOUT", "RATE_LIMITED", "PARSE_ERROR", "NO_CONTEXT",
            "UNSUPPORTED_CLAIM", "UPSTREAM_ERROR"} <= set(ERROR_CODES)
    assert all(v.strip() for v in ERROR_CODES.values())


# ---------- 5. 溯源、日志与成本护栏 ----------
def test_sources_are_traceable(client: TestClient):
    # 必须用同一个 query 比对：回答的 sources 来自本次检索命中集合
    q = "Gemini 免费层"
    r = post(client, q)
    doc_ids = [s["doc_id"] for s in r["sources"]]
    assert doc_ids
    hits = client.get("/api/v1/discover", params={"q": q}).json()["hits"]
    allowed = {h["doc_id"] for h in hits}
    assert set(doc_ids) <= allowed


def test_structured_log_fields(client: TestClient):
    r = post(client, "DeepSeek-V3 免费额度")
    logs = client.get("/api/v1/logs?limit=50").json()
    row = next((x for x in logs if x["request_id"] == r["request_id"]), None)
    assert row is not None
    assert row["prompt_version"] == settings.PROMPT_VERSION
    assert row["model"] and row["latency_ms"] >= 0 and row["ttft_ms"] >= 0
    assert row["token_usage"] >= 0


def test_token_budget_under_500(client: TestClient):
    r = post(client, "Gemini 2.0 Flash 还有免费额度吗？耗尽后会自动切到哪家？")
    assert 0 < r["token_usage"] < 500


def test_llm_parameters_are_explicit():
    assert settings.LLM_TIMEOUT_MS > 0
    assert settings.LLM_TEMPERATURE == 0.2
    assert settings.LLM_MAX_TOKENS > 0


# ---------- 6. 真实接入向导（预设 / 测试 / 保存 / 移除） ----------
def test_connect_presets_are_beginner_ready(client: TestClient):
    presets = client.get("/api/v1/connect/presets").json()
    assert len(presets) >= 3
    assert any(p["recommended"] for p in presets)
    for p in presets:
        assert p["api_base"].startswith("https://"), p["id"]
        assert p["key_url"].startswith("https://") and p["signup_url"].startswith("https://")
        assert len(p["steps"]) >= 3, f"{p['id']} 缺少手把手步骤"
        assert not p.get("api_key"), "预设不得包含密钥"


def test_connect_test_missing_key_is_actionable(client: TestClient):
    r = client.post("/api/v1/connect/test",
                    json={"provider_id": "zhipu", "api_key": ""}).json()
    assert r["ok"] is False
    assert r["error_code"] == "INVALID_KEY"
    assert r["hint"], "失败时必须给出下一步建议"


def test_connect_configure_then_remove(client: TestClient):
    before = client.get("/api/v1/connect/status").json()
    assert not any(x["configured"] for x in before)

    r = client.post("/api/v1/connect/configure", json={
        "provider_id": "zhipu", "api_key": "unit-test-key-1234567890",
        "model": "GLM-4.7-Flash",
    })
    assert r.status_code == 200
    assert "unit-test-key-1234567890" not in r.text, "响应不得回显完整密钥"
    masked = r.json()["data"]["key_masked"]
    assert "••••" in masked

    status = client.get("/api/v1/connect/status").json()
    row = next(x for x in status if x["id"] == "zhipu")
    assert row["configured"] and row["key_masked"] == masked
    # 保存即启用：总览切换到 LIVE
    assert client.get("/api/v1/overview").json()["demo_mode"] is False
    assert client.get("/api/v1/overview").json()["active_provider"] == "zhipu"

    assert client.delete("/api/v1/connect/zhipu").status_code == 200
    assert client.get("/api/v1/overview").json()["active_provider"] is None
    assert not any(x["configured"] for x in client.get("/api/v1/connect/status").json())


def test_catalog_sources_are_real_urls(client: TestClient):
    hits = client.get("/api/v1/discover", params={"q": "Groq 免费额度"}).json()["hits"]
    assert hits and hits[0]["url"].startswith("https://")
    meta = client.get("/api/v1/discover", params={"q": "Groq 免费额度"}).json()["catalog"]
    assert meta["last_verified"] == "2026-11-13" and meta["official_count"] >= 8
    catalog = json.loads((ROOT / "server" / "data" / "free_tier_catalog.json").read_text(encoding="utf-8"))
    for e in catalog["entries"]:
        if e["verification"] != "internal":
            assert e["source_url"].startswith("https://"), f"{e['doc_id']} 来源必须是可核验外链"
    assert not any("docs.local" in e["source_url"] for e in catalog["entries"])
