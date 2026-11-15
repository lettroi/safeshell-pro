"""上游适配层：mock（默认、确定性、可离线复现）与 http（真实上游骨架）。

所有 LLM 调用都必须显式传入 timeout / temperature / max_tokens；
异常按 LLM_TIMEOUT / RATE_LIMITED / UPSTREAM_ERROR / PARSE_ERROR 分类映射。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import CATALOG_LAST_VERIFIED, settings


class UpstreamTimeout(Exception):
    code = "LLM_TIMEOUT"


class RateLimited(Exception):
    code = "RATE_LIMITED"


class UpstreamFailure(Exception):
    code = "UPSTREAM_ERROR"


class ParseFailure(Exception):
    code = "PARSE_ERROR"


@dataclass
class LLMOutput:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int

    @property
    def token_usage(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def estimate_tokens(text: str) -> int:
    """mock 上游的确定性计数口径：中文按字、ASCII 按 4 字符 1 token 估算。"""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return max(1, cjk + (other + 3) // 4)


class MockLLM:
    """确定性上游：相同输入 + 相同 seed 永远得到相同输出，便于回归测试。"""

    async def complete(self, prompt: str, *, hits: list[dict[str, Any]], timeout: float,
                       temperature: float, max_tokens: int,
                       inject: str | None = None) -> LLMOutput:
        t0 = time.perf_counter()
        # 显式使用传入的参数，防止调用方忘记设置
        assert timeout > 0 and 0 <= temperature <= 2 and max_tokens > 0
        if inject == "timeout":
            raise UpstreamTimeout("mock 注入：上游超时")
        if inject == "rate_limit":
            raise RateLimited("mock 注入：429 quota_exceeded")
        if inject == "parse_error":
            return LLMOutput("这不是 JSON <<<PARSE_ERROR>>>", settings.MODEL_NAME,
                             estimate_tokens(prompt), 24, int((time.perf_counter() - t0) * 1000))

        # 只取首个命中的前 80 字：比早先「两段各 45 字」信息更完整、可读性更好，
        # 同时守住单轮 500 token 预算；完整政策以 sources 里的官方链接为准。
        primary = hits[0]["snippet"][:80].rstrip("。") if hits else ""
        payload = {
            "answer": f"依据政策清单（{CATALOG_LAST_VERIFIED} 核实）：{primary}。以厂商官方文档为准。",
            "steps": ["查看切换策略", "注入 429 演练", "按 request_id 查日志"],
            "sources": [h["doc_id"] for h in hits[:3]],
            "confidence": 0.86,
        }
        text = json.dumps(payload, ensure_ascii=False)
        return LLMOutput(text, settings.MODEL_NAME, estimate_tokens(prompt),
                         estimate_tokens(text), int((time.perf_counter() - t0) * 1000))


class HttpLLM:
    """真实上游：OpenAI 兼容 /chat/completions；端点、密钥、模型按数据源注入。"""

    def __init__(self, api_base: str, api_key: str, model: str) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def complete(self, prompt: str, *, hits: list[dict[str, Any]], timeout: float,
                       temperature: float, max_tokens: int,
                       inject: str | None = None) -> LLMOutput:
        t0 = time.perf_counter()
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        host = urlparse(self.api_base).hostname
        trust_env = host not in {"127.0.0.1", "localhost", "::1"}
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=trust_env) as client:
                resp = await client.post(f"{self.api_base}/chat/completions",
                                         headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeout(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise UpstreamFailure(str(exc)) from exc
        if resp.status_code == 429:
            raise RateLimited("HTTP 429 quota_exceeded")
        if resp.status_code >= 500:
            raise UpstreamFailure(f"HTTP {resp.status_code}")
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ParseFailure("上游响应格式无法解析") from exc
        return LLMOutput(text, self.model,
                         int(usage.get("prompt_tokens", estimate_tokens(prompt))),
                         int(usage.get("completion_tokens", estimate_tokens(text))),
                         int((time.perf_counter() - t0) * 1000))


def get_llm(provider_id: str | None = None) -> MockLLM | HttpLLM:
    """按 provider_id / 当前启用的接入配置选择真实上游；否则回落演示模式。"""
    from .connect import active_provider_id, get_credential  # 延迟导入，避免循环

    target = provider_id or active_provider_id()
    if target:
        cred = get_credential(target)
        if cred:
            return HttpLLM(cred["api_base"], cred["api_key"], cred["model"])
    if settings.PROVIDER_MODE == "http":
        return HttpLLM(settings.UPSTREAM_BASE_URL, settings.UPSTREAM_API_KEY,
                       settings.MODEL_NAME)
    return MockLLM()


class MockUsageAdapter:
    """额度快照：确定性小幅消耗，保证每次轮询都能观察到状态变化。"""

    def __init__(self) -> None:
        self._ticks = 0

    def snapshot(self, provider: dict[str, Any]) -> int:
        self._ticks += 1
        if provider["unlimited"]:
            return provider["used"]
        step = 1 if provider["unit"] in {"RPD", "TPM"} else 128
        if provider["id"] == "gemini":
            return provider["quota_limit"]  # 主线：Gemini 演示已耗尽
        return min(provider["quota_limit"], provider["used"] + step)


class HttpUsageAdapter:
    """真实 Usage API 骨架：GET {base}/usage?provider=id -> {"used": int}。"""

    async def snapshot(self, provider: dict[str, Any]) -> int:
        try:
            async with httpx.AsyncClient(timeout=settings.timeout_seconds) as client:
                resp = await client.get(
                    f"{settings.UPSTREAM_BASE_URL}/usage",
                    params={"provider": provider["id"]},
                    headers={"Authorization": f"Bearer {settings.UPSTREAM_API_KEY}"},
                )
                resp.raise_for_status()
                return int(resp.json()["used"])
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            raise UpstreamFailure(f"usage 拉取失败：{exc}") from exc
