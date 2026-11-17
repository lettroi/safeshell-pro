"""余量 · FREE TIER ROUTER — FastAPI 入口。

/api/v1/* 为 JSON API；/docs 为 Swagger（每个接口含 description 与请求示例）。
若 web/dist 已构建，则由 FastAPI 直接托管前端静态资源。
"""
from __future__ import annotations

import csv
import io
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import assistant, cache, connect, db, knowledge, router_engine as engine, cost, rate_limit
from .raw import (RawCompletionRequest, raw_completion, warmup_models, audit_fields,
                  ModelDisabledError, ModelCoolingDownError)
from .agent import run_chat
from .bus import bus
from .config import (CATALOG_LAST_VERIFIED, ERROR_CODES, ROOT, settings)
from .logging_config import log_event
from .models import (ChatRequest, ChatResponse, ConnectConfigureRequest,
                     ConnectPresetOut, ConnectStatusOut, ConnectTestOut,
                     ConnectTestRequest, ConversationDetailOut, ConversationOut,
                     DiscoverOut, ErrorResponse, EventOut, HealthOut, LogOut, MessageOut,
                     OkOut, OverviewOut, ProviderCreate, ProviderOut, SimulateOut,
                     SourceOut, StrategyOut, StrategyPatch, SwitchRequest, FeedbackRequest,
                     AuditReportOut, AuditLogOut, AuditModelBreakdown, AuditModeBreakdown,
                     AuditVerifyOut, PolicyOut, PolicyPatch, ModelCatalogOut, ModelStatusPatch)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime()          # fail-fast：配置错误在启动阶段暴露
    db.init_db()
    cache.invalidate()
    engine.start_poller()
    log_event("INFO", "startup", "服务启动完成", provider_mode=settings.PROVIDER_MODE,
              prompt_version=settings.PROMPT_VERSION)
    if settings.WARMUP_MODELS.strip():
        warmed = await warmup_models()
        log_event("INFO", "startup", f"模型预热完成：{warmed or '无'}")
    yield
    await engine.stop_poller()
    log_event("INFO", "shutdown", "服务已停止")


app = FastAPI(
    title="余量 · FREE TIER ROUTER API",
    version="1.0.0",
    description=(
        "免费大模型 API 额度监控、耗尽预警与自动切换网关。"
        f"政策知识库为 {CATALOG_LAST_VERIFIED} 官方核实快照（离线静态，不实时抓取，来源见 /api/v1/discover）；"
        "额度表、Usage 轮询与模型回答在 PROVIDER_MODE=mock 下仍为演示数据，不代表厂商实时用量。"
        "错误统一以 error_code 返回，不暴露堆栈。"
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_list, allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or "srv-" + uuid.uuid4().hex[:10]
    request.state.request_id = rid
    # 可选共享令牌：默认空 = 完全不鉴权（本机 / 内网零配置）。
    # 一旦配置了 SAFESHELL_API_TOKEN，除 health 外的所有 /api/v1 端点都需带令牌，
    # 防止互联网部署时任何人都能改策略、看审计、用光免费额度。
    token = settings.SAFESHELL_API_TOKEN.strip()
    path = request.url.path
    if token and path.startswith("/api/v1/") and path != "/api/v1/health":
        supplied = request.headers.get("X-API-Token", "")
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = supplied or auth[7:].strip()
        if not secrets.compare_digest(supplied, token):
            return JSONResponse(
                status_code=401,
                content=ErrorResponse(error_code="UNAUTHORIZED",
                                      message=ERROR_CODES["UNAUTHORIZED"],
                                      request_id=rid).model_dump())
    started = time.perf_counter()
    response = await call_next(request)
    # 任何写操作后让聚合缓存立刻失效，保证控制台改完即可见
    if request.method not in {"GET", "HEAD", "OPTIONS"} and path.startswith("/api/v1/"):
        cache.invalidate("overview", "models")
    response.headers["X-Request-ID"] = rid
    response.headers["X-Response-Time-Ms"] = str(int((time.perf_counter() - started) * 1000))
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    rid = getattr(request.state, "request_id", "srv-unknown")
    log_event("ERROR", "unhandled", f"{type(exc).__name__}: {exc}", request_id=rid)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(error_code="UPSTREAM_ERROR",
                              message=ERROR_CODES["UPSTREAM_ERROR"], request_id=rid).model_dump(),
    )


def _events(limit: int = 30) -> list[EventOut]:
    return [EventOut(**e) for e in engine.recent_events(limit)]


def _logs(limit: int = 40) -> list[LogOut]:
    rows = db.fetch_all("SELECT * FROM request_logs ORDER BY ts DESC LIMIT ?", (limit,))
    return [LogOut(request_id=r["request_id"], ts=r["ts"], intent=r["intent"],
                   model=r["model"], prompt_version=r["prompt_version"],
                   latency_ms=r["latency_ms"], ttft_ms=r["ttft_ms"],
                   token_usage=r["token_usage"], error_code=r["error_code"],
                   sources=json.loads(r["sources"] or "[]"), routed_by=r["routed_by"])
            for r in rows]


@app.get("/api/v1/health", response_model=HealthOut, summary="健康检查",
         description="只检查进程、配置与数据库，不依赖 LLM；供容器 healthcheck 使用。",
         response_description="服务状态与当前运行模式")
async def health() -> HealthOut:
    db_ok = True
    try:
        db.fetch_one("SELECT 1 AS ok")
    except sqlite3.Error:
        db_ok = False
    return HealthOut(status="ok" if db_ok else "degraded", provider_mode=settings.PROVIDER_MODE,
                     prompt_version=settings.PROMPT_VERSION, db_ok=db_ok,
                     poller_alive=engine.poller_alive())


@app.get("/api/v1/overview", response_model=OverviewOut, summary="额度总览聚合数据",
         description="一次返回数据源表格、策略、KPI、24h 趋势、告警事件与结构化日志，供三视图共用同一份状态。",
         response_description="聚合视图")
async def overview() -> OverviewOut:
    hit = cache.get("overview")
    if hit is not None:
        return hit
    s = engine.get_strategy()
    active = connect.active_provider_id()
    out = OverviewOut(
        providers=[ProviderOut(**p) for p in engine.get_providers_view()],
        strategy=StrategyOut(**s), events=_events(20), logs=_logs(20),
        kpi=_kpi_model(), trend=engine.trend_points(),
        # 一旦保存了真实接入配置，整个工作台即切到 LIVE（含对话测试台）
        demo_mode=settings.DEMO_MODE and not active, active_provider=active,
    )
    cache.put("overview", out, 3.0)
    return out


def _kpi_model():
    from .models import KPISummary
    return KPISummary(**engine.kpi())


@app.get("/api/v1/providers", response_model=list[ProviderOut], summary="列出全部数据源",
         description="返回统一单位口径的额度与状态；status 由服务端判定，限流中的源不会被误标为已耗尽。")
async def list_providers() -> list[ProviderOut]:
    return [ProviderOut(**p) for p in engine.get_providers_view()]


@app.post("/api/v1/providers", response_model=ProviderOut, status_code=201,
          summary="接入新数据源", description="从「发现免费 API」抽屉一键接入；密钥不入库，只返回脱敏占位。")
async def create_provider(payload: ProviderCreate) -> ProviderOut:
    body = payload.model_dump()
    try:
        db.execute(
            """INSERT INTO providers
               (id,name,vendor,unit,used,quota_limit,reset_at,endpoint,tier,free,rpm,
                unlimited,limited,enabled,key_hint,updated_at)
               VALUES (?,?,?,?,0,?,?,?,?,?,?,?,0,1,?,?)""",
            (body["id"], body["name"], body["vendor"], body["unit"], body["limit"],
             body["reset_at"], body["endpoint"], body["tier"], body["free"], body["rpm"],
             int(body["unlimited"]), "sk-••••7f3a", db.now_iso()),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=f"数据源 {body['id']} 已存在") from exc
    db.add_event("INFO", "source_added", f"已接入新数据源 {body['name']}", body["id"])
    row = db.fetch_one("SELECT * FROM providers WHERE id = ?", (body["id"],))
    s = engine.get_strategy()
    return ProviderOut(**engine.provider_view(row, s["current_id"], s["threshold"]))


@app.get("/api/v1/strategy", response_model=StrategyOut, summary="读取切换策略",
         description="含自动切换总开关、预警阈值、备胎链路、通知渠道与累计切换次数。")
async def get_strategy() -> StrategyOut:
    return StrategyOut(**engine.get_strategy())


@app.patch("/api/v1/strategy", response_model=StrategyOut, summary="更新切换策略",
           description="只更新传入的字段；阈值范围 [0.01, 1.0]，备胎链路按数组顺序作为优先级。")
async def patch_strategy(payload: StrategyPatch) -> StrategyOut:
    updated = engine.patch_strategy(payload.model_dump(exclude_none=True))
    db.add_event("INFO", "strategy_updated", "切换策略已更新", None, None)
    return StrategyOut(**updated)


@app.post("/api/v1/switch", response_model=OkOut, summary="切换生效模型 / 撤销切换",
          description="reason=manual 切到指定源；reason=undo 回退到上一模型。切换会同步事件流与对话系统提示卡。")
async def switch(payload: SwitchRequest) -> OkOut:
    if payload.reason == "undo":
        result = engine.undo_switch()
    else:
        try:
            result = engine.switch_to(payload.provider_id, payload.reason)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return OkOut(ok=True, message="切换完成" if result.get("changed") else "无需切换", data=result)


@app.post("/api/v1/simulate/429", response_model=SimulateOut, summary="注入一次 429 耗尽演练",
          description="耗尽当前源 → 校验备胎链路 → 自动切换 → 用同一 request_id 重放请求成功；关闭自动切换时只告警。")
async def simulate_429() -> SimulateOut:
    result = engine.simulate_429()
    return SimulateOut(**result)


@app.post("/api/v1/refresh", response_model=OkOut, summary="手动触发一次额度轮询",
          description="立即调用 Usage 适配器、写入快照并执行阈值/耗尽检查；失败按退避重试。")
async def manual_refresh() -> OkOut:
    await engine.poll_once()
    return OkOut(ok=True, message="轮询完成", data={"events": len(engine.recent_events(5))})


@app.get("/api/v1/events", response_model=list[EventOut], summary="告警与切换事件流",
         description="按时间倒序返回 WARN / ROUTE / NOTIFY / INFO 等事件。")
async def events(limit: int = Query(30, ge=1, le=200)) -> list[EventOut]:
    return _events(limit)


@app.get("/api/v1/events/stream", summary="SSE 实时阶段推送",
         description=("订阅后端真实阶段：parse / retrieve / quota_check / route / generate / respond，"
                      "以及 warning / switch / drill 通知。前端禁止用定时器模拟假进度。"),
         response_class=StreamingResponse)
async def event_stream(request_id: str | None = Query(None, description="只接收该 request_id 的事件")):
    return StreamingResponse(
        bus.stream(request_id), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/logs", response_model=list[LogOut], summary="结构化请求日志",
         description="含 request_id / model / prompt_version / latency_ms / ttft_ms / token_usage / error_code。")
async def logs(limit: int = Query(40, ge=1, le=200)) -> list[LogOut]:
    return _logs(limit)


@app.get("/api/v1/conversations", response_model=list[ConversationOut], summary="会话列表",
         description="返回会话标题、累计 token、请求数与自动切换次数。")
async def conversations() -> list[ConversationOut]:
    rows = db.fetch_all("SELECT * FROM conversations ORDER BY updated_at DESC")
    return [ConversationOut(id=r["id"], title=r["title"], total_tokens=r["total_tokens"],
                            request_count=r["request_count"], switch_count=r["switch_count"],
                            updated_at=r["updated_at"]) for r in rows]


@app.get("/api/v1/conversations/{cid}", response_model=ConversationDetailOut,
         summary="会话详情与消息", description="含 role=system 的自动切换提示卡与来源引用。")
async def conversation_detail(cid: str) -> ConversationDetailOut:
    row = db.fetch_one("SELECT * FROM conversations WHERE id = ?", (cid,))
    if not row:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = db.fetch_all(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC", (cid,))
    return ConversationDetailOut(
        conversation=ConversationOut(id=row["id"], title=row["title"],
                                     total_tokens=row["total_tokens"],
                                     request_count=row["request_count"],
                                     switch_count=row["switch_count"],
                                     updated_at=row["updated_at"]),
        messages=[MessageOut(id=m["id"], role=m["role"], content=m["content"],
                             model=m["model"], request_id=m["request_id"],
                             sources=json.loads(m["sources"] or "[]"),
                             created_at=m["created_at"]) for m in messages],
    )


@app.post("/api/v1/chat", response_model=ChatResponse, summary="统一对话网关（六步流水线）",
          description=("parse → retrieve → quota_check → route → generate → respond；"
                       "检索为空短路返回 NO_CONTEXT；429 自动切换重放；超时/限流/解析失败返回结构化 error_code。"),
          response_description="回答、来源、阶段耗时与可追溯字段")
async def chat(payload: ChatRequest) -> ChatResponse:
    return await run_chat(payload)


@app.post("/api/v1/chat/completions", response_model=ChatResponse,
          summary="统一对话网关（OpenAI 兼容别名）",
          description="与 /api/v1/chat 同一流水线，便于把现有客户端的 base_url 直接指向本网关。")
async def chat_compat(payload: ChatRequest) -> ChatResponse:
    return await run_chat(payload)


@app.get("/api/v1/discover", response_model=DiscoverOut, summary="检索免费 API 政策知识库",
         description="带 lang/version metadata 过滤的本地检索；无命中直接短路返回 NO_CONTEXT，不调用大模型。")
async def discover(q: str = Query(..., min_length=1, max_length=200,
                                  description="搜索词，如 gemini 免费额度")) -> DiscoverOut:
    hits = knowledge.search(q)
    meta = knowledge.catalog_meta()
    if not hits:
        return DiscoverOut(hits=[], answer=ERROR_CODES["NO_CONTEXT"],
                           error_code="NO_CONTEXT", catalog=meta)
    return DiscoverOut(hits=[SourceOut(doc_id=h["doc_id"], title=h["title"],
                                       url=h["url"], score=h["score"]) for h in hits],
                       answer=f"命中 {len(hits)} 条政策条目（清单 {CATALOG_LAST_VERIFIED} 核实，来源为各厂商官方页）",
                       error_code=None, catalog=meta)


@app.get("/api/v1/connect/presets", response_model=list[ConnectPresetOut],
         summary="可接入的免费大模型平台清单",
         description="返回各平台的 OpenAI 兼容地址、默认免费模型、官方注册 / Key 页面、免费政策与手把手步骤；已配置状态一并返回，绝不回显密钥。")
async def connect_presets() -> list[ConnectPresetOut]:
    return [ConnectPresetOut(**p) for p in connect.preset_out()]


@app.get("/api/v1/connect/status", response_model=list[ConnectStatusOut],
         summary="接入状态（脱敏）", description="用于页面判断当前是演示模式还是已接入真实模型。")
async def connect_status() -> list[ConnectStatusOut]:
    return [ConnectStatusOut(**p) for p in connect.status_out()]


@app.post("/api/v1/connect/test", response_model=ConnectTestOut,
          summary="测试连接（不保存密钥）",
          description="发一条极小的真实 /chat/completions 请求，校验 Key、端点与模型名；失败时返回可读错误码与下一步建议。")
async def connect_test(payload: ConnectTestRequest) -> ConnectTestOut:
    result = await connect.test_connection(
        payload.provider_id, payload.api_key, payload.api_base, payload.model)
    return ConnectTestOut(**result)


@app.post("/api/v1/connect/configure", response_model=OkOut,
          summary="保存接入配置并启用真实模型",
          description="密钥只存服务端本地 SQLite，响应只返回脱敏占位。保存后 /api/v1/chat 带 provider_id 即走该厂商。")
async def connect_configure(payload: ConnectConfigureRequest) -> OkOut:
    try:
        result = connect.configure(payload.provider_id, payload.api_key,
                                   payload.api_base, payload.model)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return OkOut(ok=True, message=f"已保存 {result['name']} 接入配置", data=result)


@app.delete("/api/v1/connect/{provider_id}", response_model=OkOut,
            summary="移除接入配置（回到演示模式）",
            description="删除本地保存的密钥与该平台的真实上游配置。")
async def connect_remove(provider_id: str) -> OkOut:
    connect.remove(provider_id)
    return OkOut(ok=True, message=f"已移除 {provider_id} 接入配置")


@app.post("/api/v1/feedback", response_model=OkOut, summary="记录赞 / 踩反馈",
          description="把反馈写入事件流与结构化日志，供 Bad Case 归因；本版本不做模型在线训练。")
async def feedback(payload: FeedbackRequest) -> OkOut:
    db.add_event("INFO", "feedback", f"{payload.vote} · {payload.reason}", None, payload.request_id)
    log_event("INFO", "feedback", payload.reason, request_id=payload.request_id)
    return OkOut(ok=True, message="反馈已记录")


@app.post("/api/v1/raw/completions", summary="纯模型透传（OpenAI 兼容 · 含限流与审计）",
          description=("把 OpenAI 格式 messages 原样转发到当前已接入的免费模型上游，不经本网关 agent。"
                       "供 ShellMate 本地优先 / 云端兜底使用；未接入真实模型时返回 DEMO mock 供联调。"
                       "按 X-Client-ID 做令牌桶限流（默认 30 RPM），每次调用落审计明细（可计费 / 可归因）。"))
async def raw_completions(request: Request, payload: RawCompletionRequest) -> dict:
    client_id = request.headers.get("X-Client-ID", "anonymous")
    mode = request.headers.get("X-ShellMate-Mode", "unknown")
    rid = getattr(request.state, "request_id", None)
    if not rate_limit.check(client_id):
        db.add_raw_audit({
            "ts": db.now_iso(), "client_id": client_id, "model": payload.model or "unknown",
            "mode": mode, "ok": False, "error_code": "RATE_LIMITED", "request_id": rid,
        })
        raise HTTPException(
            status_code=429,
            detail=ErrorResponse(error_code="RATE_LIMITED",
                                 message=ERROR_CODES["RATE_LIMITED"], request_id=rid).model_dump())
    t0 = time.perf_counter()
    try:
        data = await raw_completion(payload)
    except ModelDisabledError as exc:
        # 模型已被团队政策停用（如不再免费）：硬拒绝，返回 403 让客户端跳到下一个候选
        latency = int((time.perf_counter() - t0) * 1000)
        db.add_raw_audit({
            "ts": db.now_iso(), "client_id": client_id, "mode": mode, "ok": False,
            "model": str(exc), "latency_ms": latency,
            "error_code": "MODEL_DISABLED", "request_id": rid,
        })
        raise HTTPException(
            status_code=403,
            detail=ErrorResponse(error_code="MODEL_DISABLED",
                                 message=ERROR_CODES["MODEL_DISABLED"], request_id=rid).model_dump())
    except ModelCoolingDownError as exc:
        # 自动熔断冷却中：快速失败让客户端立刻切下一个模型，不再白等上游超时
        db.add_raw_audit({
            "ts": db.now_iso(), "client_id": client_id, "mode": mode, "ok": False,
            "model": str(exc), "latency_ms": 0,
            "error_code": "MODEL_COOLDOWN", "request_id": rid,
        })
        raise HTTPException(
            status_code=429,
            detail=ErrorResponse(error_code="MODEL_COOLDOWN",
                                 message=ERROR_CODES["MODEL_COOLDOWN"], request_id=rid).model_dump())
    except RuntimeError as exc:
        # raw 透传层的结构化错误（RAW_TIMEOUT / RAW_UPSTREAM_ERROR / RAW_HTTP_xxx）
        latency = int((time.perf_counter() - t0) * 1000)
        code = str(exc)[:64]
        db.add_raw_audit({
            "ts": db.now_iso(), "client_id": client_id, "mode": mode, "ok": False,
            "model": payload.model or "unknown", "latency_ms": latency,
            "error_code": code, "request_id": rid,
        })
        status = 429 if code == "RAW_HTTP_429" else 502
        raise HTTPException(
            status_code=status,
            detail=ErrorResponse(error_code=code, message=ERROR_CODES.get(code, ERROR_CODES["UPSTREAM_ERROR"]),
                                 request_id=rid).model_dump())
    except Exception as exc:
        latency = int((time.perf_counter() - t0) * 1000)
        db.add_raw_audit({
            "ts": db.now_iso(), "client_id": client_id, "mode": mode, "ok": False,
            "model": payload.model or "unknown", "latency_ms": latency,
            "error_code": str(exc)[:64], "request_id": rid,
        })
        raise
    latency = int((time.perf_counter() - t0) * 1000)
    usage = data.get("usage", {}) or {}
    est = cost.estimate(int(usage.get("prompt_tokens", 0) or 0),
                        int(usage.get("completion_tokens", 0) or 0))
    # 只落风险等级与命令哈希，绝不落命令原文（隐私红线）
    risk, command_hash = audit_fields(data)
    db.add_raw_audit({
        "ts": db.now_iso(), "client_id": client_id, "mode": mode, "ok": True,
        "model": data.get("model") or payload.model or "unknown",
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
        "latency_ms": latency, "billed": est["billed"], "saved": est["saved"],
        "risk": risk, "command_hash": command_hash, "request_id": rid,
    })
    return data


@app.get("/api/v1/audit/report", response_model=AuditReportOut, summary="审计与成本节省报告",
         description="聚合 raw 透传端点的调用量、token、延迟与「免费档相对付费等价模型」的节省额；"
                    "可按 client_id 过滤。这是 SafeShell Pro 成本管控卖点的数据底座。")
async def audit_report(client_id: str | None = Query(None, max_length=64,
                                                     description="按客户端过滤；为空则全团队")) -> AuditReportOut:
    rep = db.raw_audit_report(client_id)
    return AuditReportOut(
        total_calls=rep["total_calls"], ok_calls=rep["ok_calls"], error_calls=rep["error_calls"],
        total_tokens=rep["total_tokens"], total_prompt_tokens=rep["total_prompt_tokens"],
        total_completion_tokens=rep["total_completion_tokens"], avg_latency_ms=rep["avg_latency_ms"],
        estimated_saved_cny=rep["estimated_saved_cny"], billed_cny=rep["billed_cny"],
        by_model=[AuditModelBreakdown(**m) for m in rep["by_model"]],
        by_mode=[AuditModeBreakdown(**m) for m in rep["by_mode"]],
        client_id=rep["client_id"],
    )


@app.get("/api/v1/audit/logs", response_model=list[AuditLogOut], summary="审计明细",
         description="倒序返回 raw 透传端点的调用审计明细，供团队控制台追溯单次命令的生成来源与成本。")
async def audit_logs(limit: int = Query(50, ge=1, le=500),
                     client_id: str | None = Query(None, max_length=64)) -> list[AuditLogOut]:
    rows = db.recent_raw_audit(limit, client_id)
    return [AuditLogOut(
        id=r["id"], ts=r["ts"], client_id=r["client_id"], model=r["model"], mode=r["mode"],
        prompt_tokens=r["prompt_tokens"], completion_tokens=r["completion_tokens"],
        total_tokens=r["total_tokens"], latency_ms=r["latency_ms"], ok=bool(r["ok"]),
        error_code=r["error_code"], risk=r["risk"], command_hash=r["command_hash"],
        billed=r["billed"], saved=r["saved"], request_id=r["request_id"],
        prev_hash=r.get("prev_hash"), row_hash=r.get("row_hash")) for r in rows]


@app.get("/api/v1/audit/verify", response_model=AuditVerifyOut, summary="校验审计哈希链完整性",
         description=("逐行重算 sha256(prev_hash + 本行规范化内容)，检测审计是否被篡改。"
                      "只存命令哈希不存原文；返回首个异常行 id。大库可用 limit 抽样。"))
async def audit_verify(limit: int | None = Query(None, ge=1, le=100000)) -> AuditVerifyOut:
    return AuditVerifyOut(**db.verify_audit_chain(limit))


@app.get("/api/v1/audit/export", summary="导出审计明细（jsonl / csv）",
         description="按时间正序导出审计行，含哈希链字段，便于离线复核或归档。")
async def audit_export(format: str = Query("jsonl", pattern=r"^(jsonl|csv)$"),
                       limit: int = Query(10000, ge=1, le=100000),
                       client_id: str | None = Query(None, max_length=64)):
    rows = db.audit_export_rows(limit, client_id)
    if format == "jsonl":
        text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
        media = "application/x-ndjson"
        ext = "jsonl"
    else:
        buf = io.StringIO()
        cols = list(rows[0].keys()) if rows else ["id"]
        writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        text, media, ext = buf.getvalue(), "text/csv", "csv"
    return PlainTextResponse(
        text, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="safeshell-audit.{ext}"'})


@app.get("/api/v1/metrics", summary="Prometheus 文本指标",
         description="QPS、按模型成功率与延迟、熔断/限流计数。供 Prometheus 抓取或人工排查。")
async def metrics() -> PlainTextResponse:
    rep = db.raw_audit_report()
    stats = db.model_latency_stats(60)
    lines = [
        "# HELP safeshell_raw_calls_total raw 透传调用总数",
        "# TYPE safeshell_raw_calls_total counter",
        f"safeshell_raw_calls_total {rep['total_calls']}",
        "# HELP safeshell_raw_errors_total raw 透传失败总数",
        "# TYPE safeshell_raw_errors_total counter",
        f"safeshell_raw_errors_total {rep['error_calls']}",
        "# HELP safeshell_raw_avg_latency_ms 平均延迟（毫秒）",
        "# TYPE safeshell_raw_avg_latency_ms gauge",
        f"safeshell_raw_avg_latency_ms {rep['avg_latency_ms']}",
    ]
    for model, s in sorted(stats.items()):
        label = model.replace('"', "").replace("\\", "")
        lines.append(f'safeshell_model_success_rate{{model="{label}"}} {s["success_rate"]}')
        lines.append(f'safeshell_model_avg_latency_ms{{model="{label}"}} {s["avg_latency_ms"]}')
        lines.append(f'safeshell_model_calls{{model="{label}"}} {s["calls"]}')
    cooldowns = db.model_health_all()
    lines.append(f"safeshell_model_cooldowns {sum(1 for h in cooldowns.values() if h.get('cooldown_remaining', 0) > 0)}")
    lines.append(f"safeshell_cache_entries {cache.stats()['entries']}")
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.get("/api/v1/policy", response_model=PolicyOut, summary="读取团队安全门控策略",
         description="返回当前团队级策略：禁用命令正则、最高风险放行等级、模型白名单、最大 token、"
                    "是否强制本地门控。客户端（如 ShellMate）拉取后在本地强制执行，保证策略不下发命令也能生效。")
async def policy_get() -> PolicyOut:
    p = db.get_policy()
    return PolicyOut(**p)


@app.put("/api/v1/policy", response_model=PolicyOut, summary="更新团队安全门控策略",
         description="管理员更新团队策略；risk_threshold 仅限 SAFE/CAUTION/DENY。更新后会由客户端下次拉取生效。")
async def policy_put(payload: PolicyPatch, request: Request) -> PolicyOut:
    updated_by = request.headers.get("X-Admin-User", "admin")
    p = db.set_policy(payload.model_dump(exclude_none=True), updated_by)
    db.add_event("INFO", "policy_updated", "团队安全门控策略已更新", None, None)
    return PolicyOut(**p)


@app.get("/api/v1/models", response_model=list[ModelCatalogOut], summary="免费模型清单（含启用状态）",
         description=("跨所有预设展开成模型级清单，标注是否已接入真实 Key 与团队级启用状态。"
                      "客户端拉取后据此过滤候选模型，并自动跳过被停用的模型。这是「政策变化不再免费即停用」的落地入口。"))
async def list_models() -> list[ModelCatalogOut]:
    hit = cache.get("models")
    if hit is not None:
        return hit
    out = []
    health = db.model_health_all()
    stats = db.model_latency_stats(60)
    for e in connect.all_model_entries():
        m = e["model"]
        h = health.get(m, {})
        s = stats.get(m, {})
        out.append(ModelCatalogOut(
            model=m, provider_id=e["provider_id"], name=e["name"],
            region=e.get("region", ""), configured=bool(e["configured"]),
            enabled=db.get_model_status(m),
            cooldown_remaining=int(h.get("cooldown_remaining", 0)),
            reason=db.model_status_reason(m),
            avg_latency_ms=int(s.get("avg_latency_ms", 0)),
            success_rate=float(s.get("success_rate", 0.0))))
    cache.put("models", out, 3.0)
    return out


@app.put("/api/v1/models/status", response_model=ModelCatalogOut, summary="启用 / 停用某个免费模型",
         description=("团队级硬开关：政策变化导致某模型不再免费时停用它，网关会直接拒绝该模型请求，"
                      "客户端下一次拉取模型清单也会自动跳过。重新免费后再启用即可。停用原因会留痕审计。"))
async def set_model_status(payload: ModelStatusPatch) -> ModelCatalogOut:
    if not payload.model.strip():
        raise HTTPException(status_code=422, detail="模型名不能为空")
    db.set_model_status(payload.model, payload.enabled, payload.reason)
    cache.invalidate("models", "overview")
    entry = next((e for e in connect.all_model_entries() if e["model"] == payload.model), None)
    health = db.model_health_all().get(payload.model, {})
    stats = db.model_latency_stats(60).get(payload.model, {})
    common = dict(enabled=payload.enabled,
                  cooldown_remaining=int(health.get("cooldown_remaining", 0)),
                  reason=db.model_status_reason(payload.model),
                  avg_latency_ms=int(stats.get("avg_latency_ms", 0)),
                  success_rate=float(stats.get("success_rate", 0.0)))
    if not entry:
        # 即便不在预设清单里，也允许作为独立开关停用
        return ModelCatalogOut(model=payload.model, provider_id="custom", name=payload.model,
                               region="", configured=True, **common)
    return ModelCatalogOut(model=entry["model"], provider_id=entry["provider_id"], name=entry["name"],
                           region=entry.get("region", ""), configured=bool(entry["configured"]),
                           **common)


# ---------------------------------------------------------------------------
# 命令小助手（面向小白的独立界面 + 生成端点）
# 只生成 · 不执行 · 不隔离：命令由免费大模型生成，安全由本地确定性门控覆盖，
# 「执行后果」为纯静态文本推演，全程不调用任何执行原语。见 server/assistant.py。
# ---------------------------------------------------------------------------
@app.get("/assistant", response_class=HTMLResponse, include_in_schema=False,
         summary="命令小助手界面")
async def assistant_page() -> HTMLResponse:
    page = ROOT / "server" / "assistant_page.html"
    if not page.is_file():
        return HTMLResponse("<h1>命令小助手页面缺失</h1>", status_code=500)
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.post("/api/v1/assistant/generate", summary="自然语言 → 安全命令 → 执行后果模拟",
          description=("把大白话转成一条 Linux 命令：复用已接入的免费模型生成，服务端严格解析行协议，"
                       "再用确定性规则引擎覆盖模型自评风险（DENY 直接清空命令），最后给出纯文本的"
                       "「执行后果模拟」。全程只生成、不执行、不隔离。"))
async def assistant_generate(payload: dict) -> dict:
    query = str(payload.get("query") or "")
    mode = str(payload.get("mode") or "nl2cmd")
    if mode not in {"nl2cmd", "cmd2nl"}:
        mode = "nl2cmd"
    model = payload.get("model")
    return await assistant.generate_command(query, mode=mode,
                                            model=str(model) if model else None)


@app.post("/api/v1/assistant/simulate", summary="单独模拟一条命令的执行后果（不执行）",
          description="输入一条命令文本，纯静态推演它执行后会引发什么结果；绝不真正执行该命令。")
async def assistant_simulate(payload: dict) -> dict:
    command = str(payload.get("command") or "")
    return assistant.simulate_command(command)


# 可选：托管已构建的 React 前端（必须放在所有 API 路由之后）。
# 只有存在 index.html 才挂载，避免 web/dist 为空目录时把 "/" 挂坏、导致 /docs 之外的路径 404。
_WEB_DIST = ROOT / "web" / "dist"
if (_WEB_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(_WEB_DIST), html=True), name="web")
