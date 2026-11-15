"""纯模型透传端点：把 OpenAI 格式的 chat/completions 请求原样转发到当前已接入的免费模型上游，再原样返回。

设计边界（务必遵守）：
- 不套 margin-console 自己的 agent（不检索政策库、不注入路由 system prompt、不做额度问答）。
  调用方（如 ShellMate）自己携带 system prompt（要求模型输出行协议），我们只负责把请求送到
  真实模型并回传其内容 —— 这样 ShellMate 的本地门控与安全结论不被本网关干扰。
- 这是给「本地优先、云端兜底」客户端用的后端。未接入任何真实模型（演示模式）时，返回一个
  内置的、明确标注 demo=true 的 mock OpenAI 响应（内容为 ShellMate 行协议样例），便于整条链路联调；
  真实 Key 到位后无需改代码即自动走真实上游。
- 任何上游异常都向上抛出，由 main.py 全局处理器转为结构化错误。
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field
from urllib.parse import urlparse

from . import connect, db
from .config import settings
from .logging_config import log_event


class ModelDisabledError(Exception):
    """该模型已被团队政策停用（如不再免费），网关应直接拒绝。"""


class ModelCoolingDownError(Exception):
    """该模型上游刚失败过，处于自动熔断冷却期，网关快速拒绝以触发客户端切换。"""

# 演示模式内置 mock：明确标注 demo，仅用于 ShellMate → 本网关 → 解析 的整条链路联调。
# 不代表真实命令质量；真实 Key 接入后由真实模型生成内容。
_DEMO_RAW_CONTENT = (
    "EXPLAIN: 从当前目录递归查找普通文件，只列出体积大于 100MB 的。这是只读操作，不会改动任何文件。\n"
    "---\n"
    "COMMAND: find . -type f -size +100M\n"
    "RISK: SAFE\n"
    "REASON: 只读取文件信息，不写入、不删除\n"
    "PARAMS: find=查找命令; .=从当前目录开始递归; -type f=只看普通文件; -size +100M=大于 100MB"
)


class RawCompletionRequest(BaseModel):
    model: str | None = Field(default=None, description="可选；不传则用当前接入模型的 model")
    messages: list[dict[str, Any]] = Field(
        ..., description="OpenAI 格式 messages（含 system/user），原样转发到上游")
    stream: bool = Field(default=False, description="当前仅支持非流式；上游以非流式调用")
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=4096)


def model_timeout(model: str) -> float:
    """按模型分级超时；未配置的用全局 RAW_UPSTREAM_TIMEOUT_S。"""
    try:
        table = json.loads(settings.RAW_MODEL_TIMEOUTS or "{}")
        value = table.get(model)
        if value is not None:
            return max(1.0, float(value))
    except (ValueError, TypeError):
        pass
    return settings.RAW_UPSTREAM_TIMEOUT_S


def audit_fields(data: dict[str, Any]) -> tuple[str | None, str | None]:
    """从上游响应提取 RISK 等级与生成命令的 SHA-256（只存哈希，不存命令原文）。

    行协议形如 `COMMAND: ...` / `RISK: SAFE|CAUTION|DENY`；解析不到就留空，
    绝不猜测命令内容，符合「不执行、不伪造」的产品红线。
    """
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return None, None
    if not isinstance(content, str):
        return None, None
    risk = None
    command_hash = None
    for line in content.splitlines():
        s = line.strip()
        up = s.upper()
        if up.startswith("RISK:"):
            v = s.split(":", 1)[1].strip().upper()
            if v in {"SAFE", "CAUTION", "DENY"}:
                risk = v
        elif up.startswith("COMMAND:"):
            cmd = s.split(":", 1)[1].strip()
            if cmd:
                command_hash = hashlib.sha256(cmd.encode("utf-8")).hexdigest()
    return risk, command_hash


async def warmup_models() -> list[str]:
    """可选启动预热：对配置的模型各发一条 max_tokens=1 的请求，消除首次冷启动。

    默认关闭（WARMUP_MODELS 为空）——免费额度有限，且预热会消耗一次调用。
    任何失败都只记日志、不阻断启动。
    """
    names = [m.strip() for m in (settings.WARMUP_MODELS or "").split(",") if m.strip()]
    done: list[str] = []
    for m in names:
        try:
            await raw_completion(RawCompletionRequest(
                model=m, messages=[{"role": "user", "content": "ping"}],
                max_tokens=1, temperature=0))
            done.append(m)
            log_event("INFO", "warmup", "模型预热完成", model=m)
        except Exception as exc:  # noqa: BLE001 - 预热失败绝不影响启动
            log_event("WARN", "warmup_failed", f"模型预热失败：{exc}", model=m)
    return done


async def raw_completion(req: RawCompletionRequest) -> dict[str, Any]:
    """按模型名解析已配置凭据，把 messages 原样转发到上游。无真实接入则返回 DEMO mock。

    多平台同 Key 可服务多模型（智谱 glm 双档、硅基流动 4 个免费模型），凭据由
    connect.resolve_credential 按模型名匹配，不再依赖单一 active_provider。
    """
    cred, model, pid = connect.resolve_credential(req.model)
    if not cred:
        # 演示模式：返回内置 mock，便于验证 ShellMate → 本网关 → 解析 的整条链路
        return {
            "id": "raw-demo",
            "object": "chat.completion",
            "model": "demo-raw-fallback",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": _DEMO_RAW_CONTENT},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "demo": True,
        }

    # 团队政策：模型不再免费时管理员已停用 → 硬拒绝（客户端也会先跳过）
    if not db.get_model_status(model):
        raise ModelDisabledError(model)
    # 自动熔断：刚超时/过载过的模型快速拒绝，避免客户端再白等一次上游 60s 超时
    if db.model_in_cooldown(model):
        raise ModelCoolingDownError(model)

    api_base = str(cred["api_base"]).rstrip("/")
    api_key = str(cred["api_key"])
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body: dict[str, Any] = {"model": model, "messages": req.messages, "stream": False}
    if req.temperature is not None:
        body["temperature"] = req.temperature
    if req.max_tokens is not None:
        body["max_tokens"] = req.max_tokens

    host = urlparse(api_base).hostname
    trust_env = host not in {"127.0.0.1", "localhost", "::1"}
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=model_timeout(model),
                                     trust_env=trust_env) as client:
            resp = await client.post(f"{api_base}/chat/completions", headers=headers, json=body)
    except httpx.TimeoutException as exc:
        log_event("WARN", "raw_timeout", f"raw 转发超时：{exc}", provider_id=pid)
        db.mark_model_failure(model, "RAW_TIMEOUT")
        raise RuntimeError("RAW_TIMEOUT") from exc
    except httpx.HTTPError as exc:
        log_event("WARN", "raw_upstream_error", f"raw 转发失败：{exc}", provider_id=pid)
        db.mark_model_failure(model, "RAW_UPSTREAM_ERROR")
        raise RuntimeError("RAW_UPSTREAM_ERROR") from exc

    latency = int((time.perf_counter() - t0) * 1000)
    if resp.status_code >= 400:
        log_event("WARN", "raw_http_error", f"raw 上游 HTTP {resp.status_code}", provider_id=pid)
        if resp.status_code == 429 or resp.status_code >= 500:
            db.mark_model_failure(model, f"RAW_HTTP_{resp.status_code}")
        raise RuntimeError(f"RAW_HTTP_{resp.status_code}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError("RAW_PARSE_ERROR") from exc
    db.mark_model_success(model)
    data.setdefault("model", model)
    log_event("INFO", "raw_completion", "raw 转发成功", provider_id=pid, latency_ms=latency, model=model)
    return data
