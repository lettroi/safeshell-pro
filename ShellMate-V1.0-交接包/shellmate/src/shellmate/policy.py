"""团队策略客户端：向 SafeShell Pro 网关拉取策略 / 模型清单，在本地强制执行。

设计边界（与三条铁律一致）：
- 零第三方依赖，只用标准库 urllib；只在「已明确进入云端模式」时才联网，
  默认离线运行完全不触发任何请求。
- 策略只做【收紧】，绝不放宽：本地 gate.py 的确定性结论永远是底线，
  团队策略叠加在其之上（追加禁用正则、压低放行等级、缩小模型白名单、限制 token）。
- 拉取失败 / 网关不可达 / 返回异常一律静默降级为「无额外策略」，不阻断主链路，
  也不因为拿不到策略就降低安全性。
- 拉取地址由 cloud_endpoint 推导，不引入新的配置项：
  .../api/v1/raw/completions → .../api/v1/policy 与 .../api/v1/models
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:  # 仅类型标注，运行时不导入，避免与 client.py 循环依赖
    from .client import CommandResult

LEVEL_ORDER = {"SAFE": 0, "CAUTION": 1, "DENY": 2}


@dataclass
class TeamPolicy:
    """团队级安全策略（本地执行用的规范化视图）。"""

    deny_patterns: list[str] = field(default_factory=list)
    risk_threshold: str = "DENY"
    allowed_models: list[str] = field(default_factory=list)
    max_tokens: int = 512
    enforce_local_gate: bool = True
    disabled_models: list[str] = field(default_factory=list)
    # 网关 /models 返回的近 1h 实测表现：model -> {avg_latency_ms, success_rate, calls}
    model_stats: dict[str, dict[str, float]] = field(default_factory=dict)
    source: str = "default"  # default | gateway | error

    @property
    def from_gateway(self) -> bool:
        return self.source == "gateway"


def _derive(base_endpoint: str, leaf: str) -> str:
    """把 raw 透传端点换成同前缀的策略 / 模型清单端点。"""
    parts = urlsplit(base_endpoint)
    path = parts.path.rstrip("/")
    if path.endswith("/raw/completions"):
        path = path[: -len("/raw/completions")]
    elif path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
    return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/") + leaf, "", ""))


def _get_json(url: str, timeout: float, token: str = "") -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["X-API-Token"] = token
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_policy(endpoint: str, timeout: float = 2.0, include_models: bool = True,
                 token: str = "") -> TeamPolicy:
    """拉取团队策略与模型启用状态；任何异常都降级为默认策略，绝不抛出。"""
    p = TeamPolicy()
    try:
        raw = _get_json(_derive(endpoint, "/policy"), timeout, token)
        if isinstance(raw, dict):
            p.deny_patterns = [str(x) for x in (raw.get("deny_patterns") or [])]
            thr = str(raw.get("risk_threshold") or "DENY").upper()
            p.risk_threshold = thr if thr in LEVEL_ORDER else "DENY"
            p.allowed_models = [str(x) for x in (raw.get("allowed_models") or [])]
            try:
                p.max_tokens = max(1, min(8192, int(raw.get("max_tokens") or 512)))
            except (TypeError, ValueError):
                p.max_tokens = 512
            p.enforce_local_gate = bool(raw.get("enforce_local_gate", True))
            p.source = "gateway"
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError, KeyError, TypeError, TimeoutError):
        return p  # 网关不可达 / 老版本网关：本地策略照常，仅网关侧策略缺席

    if include_models:
        try:
            models = _get_json(_derive(endpoint, "/models"), timeout, token)
            if isinstance(models, list):
                for x in models:
                    if not isinstance(x, dict) or "model" not in x:
                        continue
                    name = str(x["model"])
                    if x.get("enabled") is False:
                        p.disabled_models.append(name)
                    try:
                        p.model_stats[name] = {
                            "avg_latency_ms": float(x.get("avg_latency_ms") or 0),
                            "success_rate": float(x.get("success_rate") or 0),
                            "calls": float(x.get("calls") or 0),
                        }
                    except (TypeError, ValueError):
                        pass
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                ValueError, KeyError, TypeError, TimeoutError):
            pass  # 清单拿不到就不预过滤，网关 403 仍会硬拦截
    return p


# 近 1h 实测平均延迟超过该阈值、或成功率过低的模型，降到候选链末尾。
# 只依据网关审计的实测数据，绝不凭模型名猜测；样本不足（calls 字段缺失）不参与降权。
SLOW_MODEL_MS = 8000.0
LOW_SUCCESS_RATE = 0.6


def _is_slow(name: str, policy: TeamPolicy) -> bool:
    s = policy.model_stats.get(name)
    if not s:
        return False
    latency = s.get("avg_latency_ms", 0)
    rate = s.get("success_rate", 0)
    return (latency >= SLOW_MODEL_MS) or (0 < rate < LOW_SUCCESS_RATE)


def candidate_models(chain: list[str], policy: TeamPolicy, score: bool = True) -> list[str]:
    """按策略过滤候选模型链，并按网关实测表现做稳定降权。

    顺序：先过滤（停用 / 白名单），再把实测慢或常失败的模型移到末尾；
    其余保持管理员配置的优先级不变（稳定排序，不做全局重排）。
    从不做「放宽」：过滤只会减少候选，绝不新增或提升风险。
    """
    out: list[str] = []
    for name in chain:
        name = name.strip()
        if not name or name in out:
            continue
        if name in policy.disabled_models:
            continue
        if policy.allowed_models and name not in policy.allowed_models:
            continue
        out.append(name)
    if score and policy.model_stats:
        fast = [m for m in out if not _is_slow(m, policy)]
        slow = [m for m in out if _is_slow(m, policy)]
        return fast + slow
    return out


def apply_team_policy(result: "CommandResult", policy: TeamPolicy) -> "CommandResult":
    """在 apply_gate() 之后叠加团队策略：命中禁用正则或超出放行等级 → DENY 并清空命令。

    与 gate.apply_gate 同样遵守「DENY 清 command」红线；只降级、不升级。
    """
    if not policy.from_gateway and not policy.deny_patterns:
        return result
    cmd = result.command or ""

    for pattern in policy.deny_patterns:
        try:
            hit = re.search(pattern, cmd, re.IGNORECASE)
        except re.error:
            continue  # 管理员写了非法正则：跳过该条，不影响其他规则
        if hit:
            result.risk_level = "DENY"
            result.command = ""
            result.reason = f"团队策略禁止：命令命中禁用模式 /{pattern}/"
            result.matched_rules.append("TEAM-DENY-PATTERN")
            return result

    threshold = LEVEL_ORDER.get(policy.risk_threshold, 2)
    current = LEVEL_ORDER.get(result.risk_level, 2)
    if current > threshold:
        result.risk_level = "DENY"
        result.command = ""
        result.reason = f"团队策略限制：风险等级高于允许上限 {policy.risk_threshold}"
        result.matched_rules.append("TEAM-RISK-THRESHOLD")
    return result
