"""Pydantic 请求 / 响应契约。所有接口禁止裸 dict。

每个请求模型都带 json_schema_extra 示例，保证 Swagger /docs 可直接试跑。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"] = Field(..., description="消息角色")
    content: str = Field(..., max_length=4000, description="消息正文")


class ChatRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "user_id": "dev_local", "message": "Gemini 2.0 Flash 还有免费额度吗？耗尽后会自动切到哪家？",
            "history": [], "conversation_id": "c1",
            "request_id": "req-demo-001", "provider_id": None, "inject": None,
        }]
    })
    user_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$",
                         description="用户标识（仅字母数字下划线中划线），日志中哈希存储")
    message: str = Field(..., min_length=1, max_length=4000, description="本轮提问")
    history: list[HistoryTurn] = Field(default_factory=list, max_length=6,
                                       description="历史轮数上限 6，防止 token 爆炸")
    conversation_id: str | None = Field(default=None, max_length=64,
                                        description="会话 ID；为空时创建新会话")
    request_id: str | None = Field(default=None, max_length=64,
                                   description="幂等键；重试时复用同一值，重新生成时用新值")
    provider_id: str | None = Field(
        default=None, max_length=48,
        description="真实上游 ID（见 /api/v1/connect/presets）。已配置凭据时走真实厂商，未配置自动回落演示模式")
    inject: Literal["timeout", "rate_limit", "parse_error", "no_context"] | None = Field(
        default=None, description="仅测试用故障注入；生产请保持 null")


class SourceOut(BaseModel):
    doc_id: str
    title: str
    url: str
    score: float = 0.0


class StageOut(BaseModel):
    stage: Literal["parse", "retrieve", "quota_check", "route", "generate", "respond"]
    elapsed_ms: int
    detail: str = ""


class ChatResponse(BaseModel):
    answer: str = Field(..., description="回答或兜底文案")
    sources: list[SourceOut] = Field(default_factory=list, description="经存在性校验的引用")
    latency_ms: int = Field(..., description="端到端总延迟，与 TTFT 分开测量")
    ttft_ms: int = Field(..., description="首个 stage 事件到达耗时")
    request_id: str
    prompt_version: str
    token_usage: int
    model: str
    intent: str
    error_code: str | None = None
    fallback_from: str | None = None
    routed_by: str | None = None
    stages: list[StageOut] = Field(default_factory=list)
    billed_amount: float = 0.0
    current_provider: str = ""


class ErrorResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"error_code": "LLM_TIMEOUT", "message": "服务繁忙，请稍后重试",
                      "request_id": "req-demo-001"}]
    })
    error_code: str
    message: str
    request_id: str


class ProviderOut(BaseModel):
    id: str
    name: str
    vendor: str
    unit: str
    used: int
    limit: int
    remaining: int
    remaining_pct: int
    usage_label: str
    reset_at: str
    endpoint: str
    tier: str
    free: str
    rpm: int
    status: Literal["active", "exhausted", "warning", "available", "unlimited", "limited"]
    unlimited: bool
    limited: bool
    enabled: bool
    key_hint: str = Field(..., description="脱敏后的 Key 占位，绝不返回明文")


class StrategyOut(BaseModel):
    current_id: str
    previous_id: str | None
    auto_switch: bool
    threshold: float
    chain: list[str]
    notify_in_app: bool
    notify_email: bool
    notify_webhook: bool
    switch_count: int
    avoided: int


class StrategyPatch(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"auto_switch": True, "threshold": 0.1, "chain": ["deepseek", "qwen", "glm"]}]
    })
    auto_switch: bool | None = None
    threshold: float | None = Field(default=None, ge=0.01, le=1.0)
    chain: list[str] | None = None
    notify_in_app: bool | None = None
    notify_email: bool | None = None
    notify_webhook: bool | None = None


class ProviderCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"id": "newsource", "name": "Demo-Free-Model", "vendor": "Demo Vendor",
                      "unit": "tokens", "limit": 1000000, "reset_at": "每月 1 日",
                      "endpoint": "https://example.local/v1/chat/completions",
                      "tier": "按月", "free": "1M tokens / 月", "rpm": 30}]
    })
    id: str = Field(..., min_length=2, max_length=48, pattern=r"^[a-z0-9_\-]+$")
    name: str = Field(..., min_length=1, max_length=80)
    vendor: str = Field(..., min_length=1, max_length=80)
    unit: Literal["tokens", "RPD", "TPM", "免费"] = "tokens"
    limit: int = Field(default=0, ge=0, le=10**12)
    reset_at: str = "每月 1 日"
    endpoint: str = Field(..., min_length=1, max_length=300)
    tier: str = "按月"
    free: str = "演示额度"
    rpm: int = Field(default=30, ge=0, le=10000)
    unlimited: bool = False


class SwitchRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"provider_id": "qwen", "reason": "manual"}]
    })
    provider_id: str = Field(..., min_length=1, max_length=48)
    reason: Literal["manual", "auto", "undo"] = "manual"


class EventOut(BaseModel):
    id: int
    ts: str
    level: str
    kind: str
    message: str
    provider_id: str | None = None
    request_id: str | None = None


class LogOut(BaseModel):
    request_id: str
    ts: str
    intent: str
    model: str
    prompt_version: str
    latency_ms: int
    ttft_ms: int
    token_usage: int
    error_code: str | None = None
    sources: list[str] = Field(default_factory=list)
    routed_by: str | None = None


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    model: str | None = None
    request_id: str | None = None
    sources: list[str] = Field(default_factory=list)
    created_at: str


class ConversationOut(BaseModel):
    id: str
    title: str
    total_tokens: int
    request_count: int
    switch_count: int
    updated_at: str


class ConversationDetailOut(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]


class TrendPoint(BaseModel):
    hour: str
    used: int


class KPISummary(BaseModel):
    current_model: str
    remaining_label: str
    available_sources: int
    today_switches: int
    avoided_requests: int
    today_tokens: int


class OverviewOut(BaseModel):
    providers: list[ProviderOut]
    strategy: StrategyOut
    events: list[EventOut]
    logs: list[LogOut]
    kpi: KPISummary
    trend: list[TrendPoint]
    demo_mode: bool
    active_provider: str | None = Field(
        default=None, description="当前启用的真实上游 ID；为 None 表示演示模式")


class HealthOut(BaseModel):
    status: str
    provider_mode: str
    prompt_version: str
    db_ok: bool
    poller_alive: bool


class SimulateOut(BaseModel):
    request_id: str
    from_provider: str
    to_provider: str
    auto_switch: bool
    replay_status: int
    message: str
    events: list[EventOut]


class DiscoverOut(BaseModel):
    hits: list[SourceOut]
    answer: str
    error_code: str | None = None
    catalog: dict[str, Any] | None = Field(
        default=None, description="政策清单元信息：版本、核实日期、条目数与核实级别统计")


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"request_id": "req-demo-001", "vote": "down", "reason": "回答缺少来源"}]
    })
    request_id: str
    vote: Literal["up", "down"]
    reason: str = Field(default="", max_length=500)


class OkOut(BaseModel):
    ok: bool
    message: str
    data: dict[str, Any] | None = None


class ConnectPresetOut(BaseModel):
    id: str
    name: str
    doc_id: str
    recommended: bool
    region: str
    api_base: str
    model: str
    model_help: str | None = None
    free_policy: str
    signup_url: str
    key_url: str
    docs_url: str
    steps: list[str] = Field(default_factory=list)
    notes: str = ""
    configured: bool = False
    key_masked: str | None = None
    updated_at: str | None = None


class ConnectStatusOut(BaseModel):
    id: str
    name: str
    configured: bool
    api_base: str
    model: str
    key_masked: str | None = None
    updated_at: str | None = None


class ConnectConfigureRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"provider_id": "zhipu", "api_key": "YOUR_API_KEY_HERE",
                      "model": "GLM-4.7-Flash"}]
    })
    provider_id: str = Field(..., min_length=1, max_length=48)
    api_key: str = Field(..., min_length=8, max_length=512, description="真实密钥，只存服务端")
    api_base: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=200)


class ConnectTestRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"provider_id": "groq", "api_key": "gsk_YOUR_KEY_HERE",
                      "model": "openai/gpt-oss-120b"}]
    })
    provider_id: str = Field(..., min_length=1, max_length=48)
    api_key: str = Field(default="", max_length=512)
    api_base: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=200)


class ConnectTestOut(BaseModel):
    ok: bool
    latency_ms: int
    model: str
    error_code: str | None = None
    message: str
    hint: str
    preview: str = ""


# ---------------------------------------------------------------------------
# SafeShell Pro：审计 / 成本 / 团队策略
# ---------------------------------------------------------------------------
class AuditLogOut(BaseModel):
    id: int
    ts: str
    client_id: str
    model: str
    mode: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    ok: bool
    error_code: str | None = None
    risk: str | None = None
    command_hash: str | None = None
    billed: float = 0.0
    saved: float = 0.0
    request_id: str | None = None
    prev_hash: str | None = None
    row_hash: str | None = None


class AuditVerifyOut(BaseModel):
    ok: bool
    checked: int
    total: int
    legacy_rows: int = 0
    first_bad_id: int | None = None
    message: str
    sampled: bool = False


class AuditModelBreakdown(BaseModel):
    model: str
    calls: int
    tokens: int
    saved: float


class AuditModeBreakdown(BaseModel):
    mode: str
    calls: int
    ok: int
    err: int


class AuditReportOut(BaseModel):
    total_calls: int
    ok_calls: int
    error_calls: int
    total_tokens: int
    total_prompt_tokens: int
    total_completion_tokens: int
    avg_latency_ms: float
    estimated_saved_cny: float
    billed_cny: float
    by_model: list[AuditModelBreakdown]
    by_mode: list[AuditModeBreakdown]
    client_id: str | None = None


class PolicyOut(BaseModel):
    deny_patterns: list[str] = Field(default_factory=list,
                                     description="禁止执行的命令/参数正则，客户端本地强制拦截")
    risk_threshold: str = Field(default="DENY",
                                description="允许通过的最高风险等级：SAFE/CAUTION/DENY")
    allowed_models: list[str] = Field(default_factory=list,
                                      description="允许客户端使用的云端模型白名单；空=不限制")
    max_tokens: int = 512
    enforce_local_gate: bool = True
    updated_at: str
    updated_by: str


class PolicyPatch(BaseModel):
    deny_patterns: list[str] | None = None
    risk_threshold: str | None = Field(default=None, pattern=r"^(SAFE|CAUTION|DENY)$")
    allowed_models: list[str] | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    enforce_local_gate: bool | None = None


class ModelCatalogOut(BaseModel):
    model: str = Field(..., description="模型名（与上游模型 ID 一致，如 glm-4-flash / Qwen/Qwen3-8B）")
    provider_id: str = Field(..., description="所属平台预设 ID")
    name: str = Field(..., description="平台名称")
    region: str = Field(default="", description="区域 / 接入说明")
    configured: bool = Field(default=False, description="服务端是否已接入真实 Key")
    enabled: bool = Field(default=True, description="团队级启用状态；false=已按政策停用")
    cooldown_remaining: int = Field(default=0, description="自动熔断剩余秒数；0=健康")
    reason: str = Field(default="", description="停用原因；空=正常启用")
    avg_latency_ms: int = Field(default=0, description="近 1h 实测平均延迟；0=暂无样本")
    success_rate: float = Field(default=0.0, description="近 1h 成功率；0=暂无样本")


class ModelStatusPatch(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"model": "glm-4.7-flash", "enabled": False,
                      "reason": "智谱免费档已取消，改用硅基流动 GLM-4-9B-0414"}]
    })
    model: str = Field(..., min_length=1, max_length=200, description="要启用/停用的模型名")
    enabled: bool = Field(..., description="true=启用，false=停用")
    reason: str = Field(default="", max_length=300, description="停用原因（留痕审计）")

