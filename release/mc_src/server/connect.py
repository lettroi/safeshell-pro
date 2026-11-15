"""真实厂商接入：预设清单、凭据管理、连接测试。

设计边界：
- 不能替用户注册账号或生成 API Key，因此「接入」= 用户填 Key → 测试 → 保存 → 按厂商真实调用。
- 凭据只存服务端本地 SQLite，API 只回脱敏值，日志不记录任何密钥。
- OpenAI 兼容端点各家有差异，端点 / 模型名全部可编辑，测试连接是启用前的硬门槛。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from . import db
from .logging_config import log_event

PRESETS_PATH = Path(__file__).resolve().parent / "data" / "connect_presets.json"

ERROR_HELP: dict[str, tuple[str, str]] = {
    "INVALID_KEY": ("密钥无效或没有权限", "检查是否复制完整、是否误用了别家平台的 Key；可到官方 Key 页重新生成一个。"),
    "BAD_ENDPOINT": ("接口地址不对", "确认 base_url 是否漏了 /v1 或 /openai 段；到官方「快速开始」文档复制完整地址。"),
    "BAD_MODEL": ("模型名不可用", "到官方模型列表复制当前可用的模型 ID，免费模型换代 / 下架很常见。"),
    "RATE_LIMITED": ("触发限流或免费额度用尽", "稍等再试，或到官方控制台查看剩余额度；也可以换一家平台。"),
    "LLM_TIMEOUT": ("连接超时", "检查网络与代理设置；国内网络优先选国内平台。"),
    "UPSTREAM_ERROR": ("上游服务暂时不可用", "稍后重试，或查看厂商状态页；也可能是 Key 没有开通对应模型权限。"),
    "PARSE_ERROR": ("返回格式不是标准 OpenAI 兼容", "确认该端点支持 /chat/completions，必要时换用对应的兼容地址。"),
}


def load_presets() -> list[dict[str, Any]]:
    data = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    if not data.get("presets"):
        raise ValueError(f"接入预设为空：{PRESETS_PATH}")
    return data["presets"]


def get_preset(provider_id: str) -> dict[str, Any] | None:
    return next((p for p in load_presets() if p["id"] == provider_id), None)


def mask_key(key: str) -> str:
    key = key.strip()
    if len(key) <= 8:
        return "••••••••"
    return f"{key[:4]}••••{key[-4:]}"


def preset_out() -> list[dict[str, Any]]:
    """给向导用：预设 + 是否已配置（绝不回显 Key）。"""
    rows = {r["provider_id"]: r for r in
            db.fetch_all("SELECT * FROM provider_credentials")}
    out = []
    for p in load_presets():
        cfg = rows.get(p["id"])
        item = {k: p.get(k) for k in (
            "id", "name", "doc_id", "recommended", "region", "api_base", "model",
            "model_help", "free_policy", "signup_url", "key_url", "docs_url",
            "steps", "notes")}
        item.update({
            "configured": cfg is not None,
            "api_base": cfg["api_base"] if cfg else p["api_base"],
            "model": cfg["model"] if cfg else p["model"],
            "key_masked": mask_key(cfg["api_key"]) if cfg else None,
            "updated_at": cfg["updated_at"] if cfg else None,
        })
        out.append(item)
    return out


def status_out() -> list[dict[str, Any]]:
    return [{k: p[k] for k in ("id", "name", "configured", "api_base", "model",
                               "key_masked", "updated_at")} for p in preset_out()]


def get_credential(provider_id: str) -> dict[str, Any] | None:
    row = db.fetch_one("SELECT * FROM provider_credentials WHERE provider_id = ?",
                       (provider_id,))
    return dict(row) if row else None


def configure(provider_id: str, api_key: str, api_base: str | None = None,
              model: str | None = None) -> dict[str, Any]:
    p = get_preset(provider_id)
    if not p:
        raise ValueError(f"未知平台：{provider_id}")
    key = (api_key or "").strip()
    if len(key) < 8:
        raise ValueError("API Key 长度不足，请检查是否复制完整（含前缀，不含空格）")
    base = (api_base or p["api_base"] or "").strip().rstrip("/")
    mdl = (model if model is not None else p.get("model") or "").strip()
    if not base:
        raise ValueError("接口地址不能为空")
    if not mdl:
        raise ValueError("该平台需要填写模型名，请到官方模型列表复制后重试")
    db.execute(
        """INSERT OR REPLACE INTO provider_credentials
           (provider_id, api_base, model, api_key, updated_at) VALUES (?,?,?,?,?)""",
        (provider_id, base, mdl, key, db.now_iso()),
    )
    log_event("INFO", "connect_saved", f"已保存 {p['name']} 接入配置",
              provider_id=provider_id, model=mdl)  # 不记录 Key
    # 保存即启用：整个工作台（含对话测试台）随后走真实模型，无需前端逐处传参
    db.set_flag("active_provider", provider_id)
    return {"provider_id": provider_id, "name": p["name"], "configured": True,
            "api_base": base, "model": mdl, "key_masked": mask_key(key)}


def active_provider_id() -> str | None:
    """当前启用的真实上游；未启用返回 None（演示模式）。"""
    pid = db.get_flag_value("active_provider")
    return pid if pid and get_credential(pid) else None


def configured_providers() -> list[dict[str, Any]]:
    return [dict(r) for r in db.fetch_all("SELECT * FROM provider_credentials")]


def resolve_credential(model: str | None) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """给定模型名，找到对应的已配置凭据（api_base + api_key）。

    返回 (credential, effective_model, provider_id)。一平台同 Key 可服务多个模型
    （如智谱 glm-4-flash / glm-4.7-flash、硅基流动 4 个免费模型），凭据按 preset 的
    `models` 列表匹配模型名，因此无需为同平台每个模型单独存凭据。

    找不到匹配时回退到 active / 任一已配置凭据（客户端未指定模型或模型名未登记时）。
    全无配置返回 (None, None, None)（演示模式）。
    """
    configured = configured_providers()
    if model:
        for cred in configured:
            preset = get_preset(cred["provider_id"]) or {}
            models = preset.get("models") or [cred["model"]]
            if model == cred["model"] or model in models:
                return cred, model, cred["provider_id"]
    pid = active_provider_id()
    if pid:
        cred = get_credential(pid)
        if cred:
            return cred, (model or cred["model"]), pid
    if configured:
        cred = configured[0]
        return cred, (model or cred["model"]), cred["provider_id"]
    return None, None, None


def all_model_entries() -> list[dict[str, Any]]:
    """跨所有预设，展开成「模型级」清单（一个预设可服务多个模型）。

    供 /api/v1/models 使用：既展示已接入模型，也展示尚未接入但支持一键接入的免费模型，
    并标注 configured。停用状态由 model_status 表单独判断。
    """
    rows = {r["provider_id"]: dict(r) for r in configured_providers()}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    # 同一模型名可能出现在多个预设里（如智谱 zhipu / zhipu-flash 共用双档），
    # 按模型名去重，且优先保留「已接入」的那条，避免前端出现重复行。
    ordered = sorted(load_presets(), key=lambda p: 0 if p["id"] in rows else 1)
    for p in ordered:
        models = p.get("models") or [p.get("model")]
        for m in models:
            if not m or m in seen:
                continue
            seen.add(m)
            cfg = rows.get(p["id"])
            out.append({
                "model": m,
                "provider_id": p["id"],
                "name": p["name"],
                "region": p.get("region", ""),
                "configured": cfg is not None,
                "api_base": cfg["api_base"] if cfg else p["api_base"],
            })
    return out


def remove(provider_id: str) -> None:
    db.execute("DELETE FROM provider_credentials WHERE provider_id = ?", (provider_id,))
    if db.get_flag_value("active_provider") == provider_id:
        remain = db.fetch_all("SELECT provider_id FROM provider_credentials LIMIT 1")
        if remain:
            db.set_flag("active_provider", remain[0]["provider_id"])
        else:
            db.clear_flag("active_provider")
    log_event("INFO", "connect_removed", "已移除接入配置", provider_id=provider_id)


def _fail(code: str, latency_ms: int, model: str, extra: str = "") -> dict[str, Any]:
    title, hint = ERROR_HELP.get(code, ERROR_HELP["UPSTREAM_ERROR"])
    return {"ok": False, "latency_ms": latency_ms, "model": model,
            "error_code": code, "message": title + (f"（{extra}）" if extra else ""),
            "hint": hint, "preview": ""}


async def test_connection(provider_id: str, api_key: str,
                          api_base: str | None = None,
                          model: str | None = None) -> dict[str, Any]:
    """发一条极小的真实请求验证 Key / 端点 / 模型名，不落库。"""
    p = get_preset(provider_id)
    if not p:
        return _fail("BAD_ENDPOINT", 0, model or "", f"未知平台 {provider_id}")
    base = (api_base or p["api_base"] or "").strip().rstrip("/")
    mdl = (model if model is not None else p.get("model") or "").strip()
    key = (api_key or "").strip()
    if not key:
        return _fail("INVALID_KEY", 0, mdl, "还没有填 API Key")
    if not mdl:
        return _fail("BAD_MODEL", 0, mdl, "模型名为空")

    url = f"{base}/chat/completions"
    body = {"model": mdl, "messages": [{"role": "user", "content": "请只回复 OK 两个字母"}],
            "max_tokens": 16, "temperature": 0}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    # 本地回环（含 Ollama / 自建网关）直连，外部厂商走系统代理设置
    host = urlparse(base).hostname
    trust_env = host not in {"127.0.0.1", "localhost", "::1"}
    try:
        async with httpx.AsyncClient(timeout=15.0, trust_env=trust_env) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        return _fail("LLM_TIMEOUT", int((time.perf_counter() - t0) * 1000), mdl)
    except httpx.HTTPError as exc:
        return _fail("UPSTREAM_ERROR", int((time.perf_counter() - t0) * 1000), mdl,
                     type(exc).__name__)
    latency = int((time.perf_counter() - t0) * 1000)

    if resp.status_code in (401, 403):
        return _fail("INVALID_KEY", latency, mdl, f"HTTP {resp.status_code}")
    if resp.status_code == 404:
        return _fail("BAD_ENDPOINT", latency, mdl, "HTTP 404")
    if resp.status_code == 429:
        return _fail("RATE_LIMITED", latency, mdl, "HTTP 429")
    if resp.status_code >= 400:
        text = resp.text[:200].lower()
        code = "BAD_MODEL" if ("model" in text or "not found" in text) else "UPSTREAM_ERROR"
        return _fail(code, latency, mdl, f"HTTP {resp.status_code}")
    try:
        preview = resp.json()["choices"][0]["message"]["content"][:120]
    except (ValueError, KeyError, IndexError, TypeError):
        return _fail("PARSE_ERROR", latency, mdl)
    log_event("INFO", "connect_test_ok", "连接测试成功", provider_id=provider_id,
              model=mdl, latency_ms=latency)  # 不记录 Key
    return {"ok": True, "latency_ms": latency, "model": mdl, "error_code": None,
            "message": "连接成功，可以保存并开始真实对话了", "hint": "模型回复已显示，说明 Key、端点、模型名都正确。",
            "preview": preview}
