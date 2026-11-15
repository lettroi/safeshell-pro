"""配置：全部可用环境变量覆盖，零第三方依赖。

隐私默认值写死在这里：cloud_enabled 永远从 False 起步，
即使环境变量里带了 key，也必须在交互里显式敲 enable-cloud + 二次确认才会生效。

cloud_fallback 是独立的、显式的「本地不可达 → 回退云端」开关（默认 False）：
仅当本机 Ollama 不可达且配置了 cloud_endpoint 时才回退，不破坏「默认不联网」的底线。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_QUANT = "Q4_K_M"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _data_dir() -> Path:
    """反馈与评测数据只写本机。Windows 上同样落到用户目录下，不进系统目录。"""
    custom = os.environ.get("SHELLMATE_DATA_DIR")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".local" / "share" / "shellmate"


@dataclass(frozen=True)
class Config:
    host: str = DEFAULT_HOST
    model: str = DEFAULT_MODEL
    quant: str = DEFAULT_QUANT
    timeout: int = 60
    # lines = 默认行协议（解释可流式，命令缓冲）；json = 严格 schema（解释不流式）
    response_format: str = "lines"
    # 锁定 non-thinking：Qwen3 的思考链会击穿 5 秒响应目标
    non_thinking: bool = True
    # 仅保留上一轮，V1.0 不做长上下文
    context_rounds: int = 1
    cloud_enabled: bool = False
    cloud_endpoint: str = ""
    cloud_api_key: str = ""
    # 网关可选共享令牌（对应服务端 SAFESHELL_API_TOKEN）。网关未开启鉴权时留空即可。
    cloud_token: str = ""
    cloud_fallback: bool = False
    # 云端单模型路由（向后兼容）。glm-4.7-flash 于 2026-11-15 因智谱免费档长期 429
    # 被网关默认停用，深度档改指向最稳最快的 glm-4-flash。
    cloud_model: str = "glm-4-flash"
    cloud_model_deep: str = "glm-4-flash"
    # 云端候选模型链（逗号分隔，按优先级）。任一模型被停用 / 过载 / 输出不合行协议时，
    # 客户端自动切到下一个，保证「政策变化不再免费」不会打断用户。
    # 5 个仍启用的免费模型；glm-4.7-flash 已停用，不再列入默认链。
    cloud_models_light: str = "glm-4-flash,Qwen/Qwen2.5-7B-Instruct,Qwen/Qwen3-8B"
    # 顺序依据 2026-11-15 云端实测（eval/baseline_cloud.json）：
    # glm-4-flash / Qwen2.5-7B 可用率 62.5% 且行协议 100%、p50 <4.5s；
    # Qwen3-8B 解析率 87.5%；GLM-4-9B-0414 p50 25.6s、R1 p50 17.6s 且可用率最低 → 兜底。
    cloud_models_deep: str = ("glm-4-flash,Qwen/Qwen2.5-7B-Instruct,Qwen/Qwen3-8B,"
                              "THUDM/GLM-4-9B-0414,deepseek-ai/DeepSeek-R1-0528-Qwen3-8B")
    # 归因标识：网关按此做限流与审计，团队控制台可看到每个客户端的用量
    cloud_client_id: str = "shellmate-cli"
    # 是否在云端模式下拉取团队策略 / 模型清单（仅收紧，失败降级为本地默认，绝不阻断）
    policy_fetch: bool = True
    mock: bool = False
    color: bool = True
    data_dir: Path = _data_dir()

    @property
    def feedback_path(self) -> Path:
        return self.data_dir / "feedback.jsonl"

    def with_changes(self, **kwargs: object) -> "Config":
        return replace(self, **kwargs)

    def cloud_ready(self) -> bool:
        return self.cloud_enabled and bool(self.cloud_endpoint)


def load_config() -> Config:
    """从环境变量构造配置。cloud_enabled 不接受环境变量开启，只能交互开启。"""
    fmt = os.environ.get("SHELLMATE_FORMAT", "lines").strip().lower()
    if fmt not in {"lines", "json"}:
        fmt = "lines"
    return Config(
        host=os.environ.get("SHELLMATE_HOST", DEFAULT_HOST).rstrip("/"),
        model=os.environ.get("SHELLMATE_MODEL", DEFAULT_MODEL),
        timeout=int(os.environ.get("SHELLMATE_TIMEOUT", "60")),
        response_format=fmt,
        cloud_endpoint=os.environ.get("SHELLMATE_CLOUD_ENDPOINT", ""),
        cloud_api_key=os.environ.get("SHELLMATE_CLOUD_API_KEY", ""),
        cloud_token=os.environ.get("SHELLMATE_CLOUD_TOKEN", ""),
        cloud_fallback=_env_bool("SHELLMATE_CLOUD_FALLBACK"),
        cloud_model=os.environ.get("SHELLMATE_CLOUD_MODEL", "glm-4-flash"),
        cloud_model_deep=os.environ.get("SHELLMATE_CLOUD_MODEL_DEEP", "glm-4-flash"),
        cloud_models_light=os.environ.get(
            "SHELLMATE_CLOUD_MODELS_LIGHT",
            "glm-4-flash,Qwen/Qwen2.5-7B-Instruct,Qwen/Qwen3-8B"),
        cloud_models_deep=os.environ.get(
            "SHELLMATE_CLOUD_MODELS_DEEP",
            "glm-4-flash,Qwen/Qwen2.5-7B-Instruct,Qwen/Qwen3-8B,"
            "THUDM/GLM-4-9B-0414,deepseek-ai/DeepSeek-R1-0528-Qwen3-8B"),
        cloud_client_id=os.environ.get("SHELLMATE_CLIENT_ID", "shellmate-cli"),
        policy_fetch=not _env_bool("SHELLMATE_NO_POLICY"),
        mock=_env_bool("SHELLMATE_MOCK"),
        color=not _env_bool("SHELLMATE_NO_COLOR"),
    )
