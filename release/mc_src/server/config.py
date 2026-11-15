"""全局配置：唯一配置入口，所有影响输出的变量都必须从这里读取。

启动时调用 validate_runtime() 做 fail-fast 校验，避免第一次调用才报 KeyError。
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

# 后端真实阶段（前端加载态文案必须与此一一对应，见 web/src/stageText.ts）
STAGES: tuple[str, ...] = (
    "parse",
    "retrieve",
    "quota_check",
    "route",
    "generate",
    "respond",
)

# 政策知识库版本：与 server/data/free_tier_catalog.json 的 catalog_version 严格一致。
# 换清单 = 换版本，旧数据保留在库里但不参与检索，便于口径回溯。
KNOWLEDGE_VERSION: str = "free-tier-2026-11-13"
CATALOG_LAST_VERIFIED: str = "2026-11-13"

# 统一错误码；前端异常态按此分发文案与按钮
ERROR_CODES: dict[str, str] = {
    "LLM_TIMEOUT": "服务繁忙，请稍后重试",
    "RATE_LIMITED": "请求过于频繁，请稍后重试",
    "UPSTREAM_ERROR": "上游服务暂时不可用，请稍后重试",
    "PARSE_ERROR": "结果格式异常，已记录，请重试",
    "NO_CONTEXT": "知识库中未找到相关信息，建议您查阅官方文档",
    "UNSUPPORTED_CLAIM": "我无法确定该答案的准确性，建议您查阅官方文档",
    "UNSAFE_INPUT": "该请求包含不被允许的内容，已拒绝处理",
    "EMPTY_INPUT": "输入为空，请重新输入",
    "MODEL_DISABLED": "该模型已按团队政策停用（可能不再免费），请改用其他可用模型",
    "MODEL_COOLDOWN": "该模型上游刚超时或过载，暂时熔断冷却中，已自动切换到其他模型",
    "UNAUTHORIZED": "缺少或错误的访问令牌（公开部署时需在请求头带 X-API-Token）",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    APP_ENV: str = "dev"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    DEMO_MODE: bool = True
    LOG_LEVEL: str = "INFO"
    LOG_PATH: str = "./logs/app.jsonl"

    PROVIDER_MODE: str = "mock"
    UPSTREAM_BASE_URL: str = "http://localhost:9000/v1"
    UPSTREAM_API_KEY: str = "YOUR_UPSTREAM_KEY_HERE"
    MODEL_NAME: str = "mock-deepseek-v3"

    # LLM 参数显式配置，代码不允许另设隐式默认值
    LLM_TIMEOUT_MS: int = 10000
    LLM_TEMPERATURE: float = 0.2
    LLM_MAX_TOKENS: int = 512
    PROMPT_VERSION: str = "quota-router-cn-v1.0.0"

    RETRIEVAL_TOP_K: int = 3
    RETRIEVAL_MIN_SCORE: float = 0.5
    MAX_INPUT_CHARS: int = 4000
    HISTORY_MAX_TURNS: int = 6
    RANDOM_SEED: int = 20261112

    DB_PATH: str = "./data/margin.db"
    POLL_INTERVAL_S: float = 6.0
    QUOTA_WARN_THRESHOLD: float = 0.10
    AUTO_SWITCH: bool = True
    FALLBACK_CHAIN: str = "deepseek,qwen,glm"
    NOTIFY_IN_APP: bool = True
    NOTIFY_EMAIL: bool = False
    NOTIFY_WEBHOOK: bool = False

    FAULT_INJECT_TIMEOUT: bool = False
    FAULT_INJECT_PARSE_ERROR: bool = False
    FAULT_INJECT_RATE_LIMIT: bool = False

    # raw 透传端点按客户端的限流（令牌桶，单位 RPM）。
    # 0 表示不限制。默认 30 对齐 GLM 免费档 RPM，避免单客户端打爆团队共享额度。
    RAW_RPM_PER_CLIENT: int = 30
    # raw 转发上游的超时（秒）。SiliconFlow 部分开源模型冷启动可达 30~40s，默认放宽到 60s。
    RAW_UPSTREAM_TIMEOUT_S: float = 60.0
    # 按模型分级超时（JSON 字符串，如 {"glm-4-flash":10,"THUDM/GLM-4-9B-0414":45}）。
    # 命中时覆盖 RAW_UPSTREAM_TIMEOUT_S，让快模型快速失败、慢模型保留冷启动余量。
    RAW_MODEL_TIMEOUTS: str = '{"glm-4-flash":12,"Qwen/Qwen2.5-7B-Instruct":20,' \
                              '"Qwen/Qwen3-8B":30,"deepseek-ai/DeepSeek-R1-0528-Qwen3-8B":45,' \
                              '"THUDM/GLM-4-9B-0414":50,"glm-4.7-flash":12}'
    # 启动预热模型（逗号分隔，留空=不预热）。免费额度有限，默认关闭；
    # 需要消除首调冷启动时设为快的 1~2 个模型，如 "glm-4-flash"。
    WARMUP_MODELS: str = ""
    # 公开部署时的可选共享令牌。默认空 = 不鉴权（本机 / 内网零配置可直接用）。
    # 设置后，所有 /api/v1 写操作、raw 透传与审计端点都需带 X-API-Token（或 Bearer）。
    # 面向互联网开放服务前务必设置，避免任何人都能改策略、看审计、用光免费额度。
    SAFESHELL_API_TOKEN: str = ""

    @property
    def cors_list(self) -> list[str]:
        return [x.strip() for x in self.CORS_ORIGINS.split(",") if x.strip()]

    @property
    def chain_list(self) -> list[str]:
        return [x.strip() for x in self.FALLBACK_CHAIN.split(",") if x.strip()]

    @property
    def timeout_seconds(self) -> float:
        return self.LLM_TIMEOUT_MS / 1000.0

    def validate_runtime(self) -> None:
        """启动 fail-fast：配置错误必须在进程启动阶段暴露。"""
        if self.PROVIDER_MODE not in {"mock", "http"}:
            raise RuntimeError("PROVIDER_MODE 只能是 mock 或 http")
        if self.PROVIDER_MODE == "http":
            if not self.UPSTREAM_BASE_URL.startswith(("http://", "https://")):
                raise RuntimeError("PROVIDER_MODE=http 时 UPSTREAM_BASE_URL 必须是合法 URL")
            if self.UPSTREAM_API_KEY.startswith("YOUR_"):
                raise RuntimeError("PROVIDER_MODE=http 时必须在 .env 配置真实 UPSTREAM_API_KEY")
        if self.LLM_TIMEOUT_MS <= 0 or self.LLM_MAX_TOKENS <= 0:
            raise RuntimeError("LLM_TIMEOUT_MS / LLM_MAX_TOKENS 必须为正数")
        if not 0 <= self.LLM_TEMPERATURE <= 2:
            raise RuntimeError("LLM_TEMPERATURE 必须落在 [0, 2]")
        if not 0 < self.QUOTA_WARN_THRESHOLD <= 1:
            raise RuntimeError("QUOTA_WARN_THRESHOLD 必须落在 (0, 1]")
        if self.PROMPT_VERSION not in {"quota-router-cn-v1.0.0"}:
            raise RuntimeError("PROMPT_VERSION 与 server/prompts/ 下的模板版本不匹配")


settings = Settings()
