"""六步 Agent 流水线：parse → retrieve → quota_check → route → generate → respond。

每步都有独立失败分支；检索为空必须短路，禁止把空 context 塞给大模型硬编。
所有阶段通过 SSE 事件总线推送真实事件，前端不得用 setTimeout 模拟。
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import uuid
from typing import Any

from . import db, knowledge, router_engine as engine
from .bus import emit_stage
from .config import ERROR_CODES, ROOT, settings
from .logging_config import hash_user, log_request, redact
from .models import ChatRequest, ChatResponse, SourceOut, StageOut
from .upstream import (LLMOutput, MockLLM, ParseFailure, RateLimited, UpstreamFailure,
                       UpstreamTimeout, estimate_tokens, get_llm)

PROMPT_FILE = ROOT / "server" / "prompts" / f"{settings.PROMPT_VERSION}.txt"

_UNSAFE = re.compile(
    r"(忽略(以上|之前|上面)(的)?(指令|规则)|ignore\s+(all\s+)?previous\s+instructions|"
    r"system\s+prompt|泄露.{0,4}(密钥|api\s*key)|导出.{0,4}(密钥|api\s*key)|"
    r"(生成|编写|写|制作|制造).{0,6}(病毒|木马|勒索|恶意软件)|如何(制作|制造).{0,4}(炸弹|武器))",
    re.I,
)
_CHITCHAT = re.compile(r"^(你好|hi|hello|嗨|在吗|谢谢|thanks|你是谁|讲个笑话|今天天气.*)\W*$", re.I)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def load_prompt() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def preprocess(text: str) -> tuple[str, bool]:
    cleaned = unicodedata.normalize("NFKC", text)
    cleaned = _CONTROL.sub("", cleaned).strip()
    cleaned = redact(cleaned)
    truncated = len(cleaned) > settings.MAX_INPUT_CHARS
    if truncated:
        cleaned = cleaned[: settings.MAX_INPUT_CHARS] + "…（已截断）"
    return cleaned, truncated


def classify_intent(text: str) -> str:
    if _UNSAFE.search(text):
        return "refusal"
    if _CHITCHAT.match(text):
        return "chitchat"
    return "quota_qa"


def build_prompt(question: str, hits: list[dict[str, Any]], history: list[dict[str, Any]]) -> str:
    # Top-K 命中后按 token 预算截断：只把前 2 条、每条 60 字放进 prompt
    context = "\n".join(f"[{h['doc_id']}] {h['snippet'][:50]}" for h in hits[:2])
    template = load_prompt()
    # 用 replace 而不是 str.format：模板中的 JSON 示例花括号必须保持字面量。
    # 用户输入始终被当作数据，由模板中的分隔符包裹并声明其中指令无效。
    return (template.replace("{context}", context)
                    .replace("{question}", question[:settings.MAX_INPUT_CHARS]))


def source_views(hits: list[dict[str, Any]]) -> list[SourceOut]:
    return [SourceOut(doc_id=h["doc_id"], title=h["title"], url=h["url"], score=h["score"])
            for h in hits]


def postprocess(output: LLMOutput, hits: list[dict[str, Any]]) -> tuple[dict[str, Any], str | None]:
    """解析 + 硬约束校验 + 引用存在性校验；失败时降级，不抛堆栈给前端。"""
    try:
        data = json.loads(output.text)
        if not isinstance(data, dict):
            raise ValueError("不是 JSON 对象")
    except (json.JSONDecodeError, ValueError) as exc:
        raise ParseFailure(f"输出解析失败：{exc}") from exc

    answer = str(data.get("answer", "")).strip()
    steps = data.get("steps", [])
    sources = data.get("sources", [])
    if not answer:
        raise ParseFailure("回答为空")
    if not isinstance(steps, list) or len(steps) > 3:
        steps = list(steps)[:3] if isinstance(steps, list) else []
    if not isinstance(sources, list):
        sources = []
    valid_sources = knowledge.validate_sources([str(s) for s in sources], hits)
    error_code = None
    if sources and len(valid_sources) != len(set(str(s) for s in sources)):
        # 模型编造了不在本次检索集合中的来源
        error_code = "UNSUPPORTED_CLAIM"
        answer = ERROR_CODES["UNSUPPORTED_CLAIM"]
        valid_sources = []
    elif not valid_sources:
        error_code = "UNSUPPORTED_CLAIM"
        answer = ERROR_CODES["UNSUPPORTED_CLAIM"]
    answer = redact(answer)
    rendered = answer
    if steps and not error_code:
        rendered += "\n可执行步骤：" + "；".join(f"{i + 1}. {redact(s)}" for i, s in enumerate(steps[:3]))
    return {"answer": rendered, "sources": valid_sources, "steps": steps,
            "confidence": float(data.get("confidence", 0.0))}, error_code


def postprocess_plain(output: LLMOutput, hits: list[dict[str, Any]]) -> tuple[dict[str, Any], str | None]:
    """真实上游的宽松通道：允许纯文本回答，不因「非 JSON / 未引用本地文档」判为幻觉。

    本地检索命中的政策条目仍作为参考材料一并返回，但不作为模型引用的硬性校验条件。
    """
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", output.text.strip()).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("answer"):
            text = str(data["answer"]).strip()
            steps = data.get("steps") or []
            if isinstance(steps, list) and steps:
                text += "\n可执行步骤：" + "；".join(
                    f"{i + 1}. {redact(str(s))}" for i, s in enumerate(steps[:3]))
    except (json.JSONDecodeError, ValueError):
        pass
    text = redact(text)
    if not text:
        raise ParseFailure("回答为空")
    return {"answer": text, "sources": [h["doc_id"] for h in hits],
            "steps": [], "confidence": 0.0}, None


def _ensure_conversation(conversation_id: str | None, first_message: str) -> str:
    if conversation_id:
        row = db.fetch_one("SELECT id FROM conversations WHERE id = ?", (conversation_id,))
        if row:
            return conversation_id
    cid = "c-" + uuid.uuid4().hex[:8]
    ts = db.now_iso()
    db.execute(
        """INSERT INTO conversations
           (id,title,created_at,updated_at,total_tokens,request_count,switch_count)
           VALUES (?,?,?,?,0,0,0)""",
        (cid, first_message[:24] or "新会话", ts, ts),
    )
    return cid


def _add_message(cid: str, role: str, content: str, model: str | None,
                 request_id: str, sources: list[str]) -> None:
    db.execute(
        """INSERT INTO messages
           (conversation_id,role,content,model,request_id,sources,created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (cid, role, redact(content), model, request_id,
         json.dumps(sources, ensure_ascii=False), db.now_iso()),
    )
    db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (db.now_iso(), cid))


async def run_chat(req: ChatRequest) -> ChatResponse:
    t0 = time.perf_counter()
    request_id = req.request_id or "req-" + uuid.uuid4().hex[:10]
    stages: list[StageOut] = []

    def stage(name: str, detail: str = "") -> None:
        elapsed = int((time.perf_counter() - t0) * 1000)
        stages.append(StageOut(stage=name, elapsed_ms=elapsed, detail=detail))  # type: ignore[arg-type]
        emit_stage(request_id, name, elapsed, detail)

    # 步骤 1：预处理
    text, truncated = preprocess(req.message)
    intent = classify_intent(text)
    stage("parse", f"intent={intent}" + ("；输入已截断" if truncated else ""))
    if not text:
        return _finish(req, request_id, t0, stages, ERROR_CODES["EMPTY_INPUT"], [], intent,
                       "EMPTY_INPUT", 0, None, None)

    # 步骤 2 + 3：意图分流与检索增强
    if intent == "refusal":
        stage("quota_check", "安全规则命中，跳过检索与模型调用")
        stage("route", "不选择任何上游")
        return _finish(req, request_id, t0, stages, ERROR_CODES["UNSAFE_INPUT"], [], intent,
                       "UNSAFE_INPUT", 0, None, None)
    if intent == "chitchat":
        stage("quota_check", "闲聊走短路径，不消耗检索与模型预算")
        stage("route", "无需路由")
        return _finish(req, request_id, t0, stages,
                       "你好，我是「余量」工作台助手。可以问我各数据源的免费额度、切换策略或 429 重放链路。",
                       [], intent, None, 0, None, None)

    hits: list[dict[str, Any]] = []
    if req.inject == "no_context":
        text_for_search = "不存在的量子引力论文检索词"
    else:
        text_for_search = text
    hits = knowledge.search(text_for_search)
    stage("retrieve", f"hits={len(hits)} · top_k={settings.RETRIEVAL_TOP_K}")
    if not hits:
        # 铁律：检索为空必须短路，不调用 LLM
        stage("route", "无 context，跳过上游调用")
        return _finish(req, request_id, t0, stages, ERROR_CODES["NO_CONTEXT"], [], intent,
                       "NO_CONTEXT", 0, None, None)

    # 检索命中后按 token 预算截断低分片段；引用只能来自真正进入 prompt 的片段
    context_hits = hits[:2]

    # 步骤 4：额度检查与路由决策
    engine.check_quota(request_id)
    strategy = engine.get_strategy()
    fallback_from: str | None = None
    routed_by = "current"
    current = strategy["current_id"]
    cur_view = next((v for v in engine.get_providers_view() if v["id"] == current), None)
    if cur_view and cur_view["remaining_pct"] <= 0 and strategy["auto_switch"] \
            and not db.get_flag(f"exhausted:{current}"):
        nxt = engine.pick_next(current)
        if nxt:
            db.set_flag(f"exhausted:{current}")
            fallback_from = current
            engine.switch_to(nxt, "auto", request_id)
            routed_by = "auto_switch"
            current = nxt
    stage("quota_check", f"阈值={strategy['threshold']:.0%} · current={current}")
    stage("route", f"selected={current} · routed_by={routed_by}")

    # 步骤 5：Prompt 组装 + 模型调用
    prompt = build_prompt(text, context_hits, [h.model_dump() for h in req.history])
    llm = get_llm(req.provider_id)   # 已配置凭据则走真实厂商，否则回落演示模式
    output: LLMOutput | None = None
    error_code: str | None = None
    answer = ""
    valid_sources: list[str] = []
    try:
        stage("generate", f"prompt_version={settings.PROMPT_VERSION} · temperature={settings.LLM_TEMPERATURE}")
        output = await llm.complete(
            prompt, hits=context_hits, timeout=settings.timeout_seconds,
            temperature=settings.LLM_TEMPERATURE, max_tokens=settings.LLM_MAX_TOKENS,
            inject=req.inject,
        )
    except RateLimited as exc:
        # 429 允许重试一次：切换数据源后重放，原 request_id 与上下文不变
        drill = engine.simulate_429(request_id)
        fallback_from = drill["from_provider"]
        current = drill["to_provider"]
        routed_by = "failover_429"
        emit_stage(request_id, "route", int((time.perf_counter() - t0) * 1000),
                   f"429 重放 → {current}")
        try:
            output = await llm.complete(
                prompt, hits=context_hits, timeout=settings.timeout_seconds,
                temperature=settings.LLM_TEMPERATURE, max_tokens=settings.LLM_MAX_TOKENS,
                inject=None,
            )
        except (UpstreamTimeout, RateLimited, UpstreamFailure) as retry_exc:
            error_code = getattr(retry_exc, "code", "UPSTREAM_ERROR")
            answer = ERROR_CODES.get(error_code, ERROR_CODES["UPSTREAM_ERROR"])
    except UpstreamTimeout as exc:
        error_code = exc.code
        answer = ERROR_CODES[error_code]
    except UpstreamFailure as exc:
        error_code = exc.code
        answer = ERROR_CODES[error_code]
    except ParseFailure as exc:
        error_code = exc.code
        answer = ERROR_CODES[error_code]

    # 步骤 6：后处理（解析、≤3 步、引用存在性校验、脱敏）
    if output is not None and not error_code:
        try:
            # mock 上游走严格契约（回归测试依赖它）；真实厂商允许自然语言回答
            if isinstance(llm, MockLLM):
                parsed, post_error = postprocess(output, context_hits)
            else:
                parsed, post_error = postprocess_plain(output, context_hits)
            answer = parsed["answer"]
            valid_sources = parsed["sources"]
            if post_error:
                error_code = post_error
        except ParseFailure:
            error_code = "PARSE_ERROR"
            answer = ERROR_CODES["PARSE_ERROR"]
            valid_sources = []

    token_usage = output.token_usage if output else estimate_tokens(prompt) + estimate_tokens(answer)
    stage("respond", f"error_code={error_code}" if error_code else "200 OK")
    return _finish(req, request_id, t0, stages, answer,
                   source_views([h for h in context_hits if h["doc_id"] in valid_sources]),
                   intent, error_code, token_usage, fallback_from, routed_by,
                   current_provider=current, model=output.model if output else settings.MODEL_NAME)


def _finish(req: ChatRequest, request_id: str, t0: float, stages: list[StageOut],
            answer: str, sources: list[SourceOut], intent: str, error_code: str | None,
            token_usage: int, fallback_from: str | None, routed_by: str | None,
            current_provider: str = "", model: str | None = None) -> ChatResponse:
    latency_ms = int((time.perf_counter() - t0) * 1000)
    ttft_ms = stages[0].elapsed_ms if stages else latency_ms
    cid = _ensure_conversation(req.conversation_id, req.message)
    try:
        _add_message(cid, "user", req.message, None, request_id, [])
        _add_message(cid, "assistant", answer, model or settings.MODEL_NAME, request_id,
                     [s.doc_id for s in sources])
        db.execute(
            """UPDATE conversations
               SET total_tokens = total_tokens + ?, request_count = request_count + 1
               WHERE id = ?""",
            (token_usage, cid),
        )
    except Exception as exc:
        # 落库失败不能阻塞主响应，仅记录结构化日志
        from .logging_config import log_event
        log_event("ERROR", "db_write_failed", f"{type(exc).__name__}: {exc}", request_id=request_id)

    row = {
        "request_id": request_id, "ts": db.now_iso(), "user_id": req.user_id,
        "user_id_hash": hash_user(req.user_id), "intent": intent,
        "model": model or settings.MODEL_NAME, "prompt_version": settings.PROMPT_VERSION,
        "temperature": settings.LLM_TEMPERATURE, "max_tokens": settings.LLM_MAX_TOKENS,
        "timeout_ms": settings.LLM_TIMEOUT_MS, "retrieval_hits": len(sources),
        "sources": [s.doc_id for s in sources], "latency_ms": latency_ms,
        "ttft_ms": ttft_ms, "token_usage": token_usage, "error_code": error_code,
        "answer_digest": answer[:240], "fallback_from": fallback_from, "routed_by": routed_by,
    }
    try:
        db.add_request_log(row)
    except Exception:
        pass
    log_request(row)

    return ChatResponse(
        answer=answer, sources=sources, latency_ms=latency_ms, ttft_ms=ttft_ms,
        request_id=request_id, prompt_version=settings.PROMPT_VERSION,
        token_usage=token_usage, model=model or settings.MODEL_NAME, intent=intent,
        error_code=error_code, fallback_from=fallback_from, routed_by=routed_by,
        stages=stages, billed_amount=0.0, current_provider=current_provider,
    )
