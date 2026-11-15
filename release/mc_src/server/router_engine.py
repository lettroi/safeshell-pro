"""额度状态机：轮询 → 阈值预警 → 耗尽 → 自动切换 → 事件/日志/对话联动。

设计取舍（沿用原型）：所有视图共用同一份服务端状态，切换一次要同步
strategy / 事件流 / 结构化日志 / 对话 system 提示卡 / SSE 通知。
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from . import db
from .bus import emit
from .config import settings
from .logging_config import log_event
from .upstream import HttpUsageAdapter, MockUsageAdapter, UpstreamFailure

_mock_usage = MockUsageAdapter()
_http_usage = HttpUsageAdapter()
_poller_task: asyncio.Task | None = None


def compact(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def usage_label(used: int, limit: int, unit: str, unlimited: bool) -> str:
    if unlimited:
        return "不限量 / 免费"
    return f"{compact(used)} / {compact(limit)} {unit}"


def remaining_pct(row: dict[str, Any]) -> int:
    if row["unlimited"]:
        return 100
    if not row["quota_limit"]:
        return 0
    return max(0, round((row["quota_limit"] - row["used"]) / row["quota_limit"] * 100))


def remaining_abs(row: dict[str, Any]) -> int:
    if row["unlimited"]:
        return -1
    return max(0, row["quota_limit"] - row["used"])


def provider_view(row: dict[str, Any], current_id: str, threshold: float) -> dict[str, Any]:
    pct = remaining_pct(row)
    if row["limited"]:
        status = "limited"          # 限流中优先，避免「1% 剩余却写已耗尽」的自相矛盾文案
    elif pct <= 0 and not row["unlimited"]:
        status = "exhausted"
    elif row["unlimited"]:
        status = "unlimited"
    elif row["id"] == current_id:
        status = "active"
    elif pct <= round(threshold * 100):
        status = "warning"
    else:
        status = "available"
    return {
        "id": row["id"], "name": row["name"], "vendor": row["vendor"], "unit": row["unit"],
        "used": row["used"], "limit": row["quota_limit"], "remaining": remaining_abs(row),
        "remaining_pct": pct,
        "usage_label": usage_label(row["used"], row["quota_limit"], row["unit"], bool(row["unlimited"])),
        "reset_at": row["reset_at"], "endpoint": row["endpoint"], "tier": row["tier"],
        "free": row["free"], "rpm": row["rpm"], "status": status,
        "unlimited": bool(row["unlimited"]), "limited": bool(row["limited"]),
        "enabled": bool(row["enabled"]), "key_hint": row["key_hint"],
    }


def get_providers_view() -> list[dict[str, Any]]:
    s = get_strategy()
    rows = db.fetch_all("SELECT * FROM providers ORDER BY id")
    return [provider_view(r, s["current_id"], s["threshold"]) for r in rows]


def get_strategy() -> dict[str, Any]:
    row = db.fetch_one("SELECT * FROM strategy WHERE id = 1")
    assert row is not None
    return {
        "current_id": row["current_id"], "previous_id": row["previous_id"],
        "auto_switch": bool(row["auto_switch"]), "threshold": row["threshold"],
        "chain": [x for x in row["chain"].split(",") if x],
        "notify_in_app": bool(row["notify_in_app"]),
        "notify_email": bool(row["notify_email"]),
        "notify_webhook": bool(row["notify_webhook"]),
        "switch_count": row["switch_count"], "avoided": row["avoided"],
    }


def patch_strategy(patch: dict[str, Any]) -> dict[str, Any]:
    s = get_strategy()
    mapping = {
        "auto_switch": ("auto_switch", lambda v: int(bool(v))),
        "threshold": ("threshold", float),
        "chain": ("chain", lambda v: ",".join(v)),
        "notify_in_app": ("notify_in_app", lambda v: int(bool(v))),
        "notify_email": ("notify_email", lambda v: int(bool(v))),
        "notify_webhook": ("notify_webhook", lambda v: int(bool(v))),
    }
    sets, vals = [], []
    for key, value in patch.items():
        if value is None or key not in mapping:
            continue
        col, conv = mapping[key]
        sets.append(f"{col} = ?")
        vals.append(conv(value))
    if sets:
        sets.append("updated_at = ?")
        vals.extend([db.now_iso(), 1])
        db.execute(f"UPDATE strategy SET {', '.join(sets)} WHERE id = ?", tuple(vals))
    return get_strategy()


def pick_next(current_id: str | None = None) -> str | None:
    s = get_strategy()
    cur = current_id or s["current_id"]
    views = {v["id"]: v for v in get_providers_view()}
    for pid in s["chain"]:
        v = views.get(pid)
        if v and pid != cur and v["status"] not in {"exhausted", "limited"} and v["enabled"]:
            return pid
    candidates = [v for v in views.values()
                  if v["id"] != cur and v["status"] not in {"exhausted", "limited"} and v["enabled"]]
    if not candidates:
        return None
    candidates.sort(key=lambda v: (-v["remaining_pct"], v["id"]))
    return candidates[0]["id"]


def switch_to(target_id: str, reason: str = "auto", request_id: str | None = None,
              insert_card: bool = True) -> dict[str, Any]:
    s = get_strategy()
    target = db.fetch_one("SELECT * FROM providers WHERE id = ?", (target_id,))
    if not target:
        raise ValueError(f"未知数据源：{target_id}")
    if target_id == s["current_id"]:
        return {"from": s["current_id"], "to": target_id, "changed": False}
    db.execute(
        "UPDATE strategy SET current_id = ?, previous_id = ?, updated_at = ? WHERE id = 1",
        (target_id, s["current_id"], db.now_iso()),
    )
    if reason == "auto":
        db.bump_counter("switch_count")
        db.bump_counter("avoided")
    ev = db.add_event("ROUTE", "switch",
                      f"{'自动' if reason == 'auto' else '手动'}切换：{s['current_id']} → {target_id}"
                      f"，上下文完整保留，请求未中断",
                      target_id, request_id)
    if insert_card:
        db.execute(
            """INSERT INTO messages
               (conversation_id,role,content,model,request_id,sources,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("c1", "system",
             f"已自动切换模型：{s['current_id']} 免费额度耗尽，已切至 {target['name']}。"
             "上下文与历史消息完整保留，本次回答继续。可点击「撤销切换」回退。",
             target_id, request_id or "", "[]", db.now_iso()),
        )
    for key in ("notified_email", "notified_webhook"):
        if get_strategy()["notify_email"] or get_strategy()["notify_webhook"]:
            db.add_event("NOTIFY", "notify_intent", f"通知渠道意图已记录（不真实投递）：{key}", target_id, request_id)
            break
    log_event("INFO", "switch", ev["message"], request_id=request_id,
              model=target_id, prompt_version=settings.PROMPT_VERSION, latency=0, token_usage=0)
    emit("switch", "auto" if reason == "auto" else "manual", ev["message"],
         request_id=request_id, from_provider=s["current_id"], to_provider=target_id)
    return {"from": s["current_id"], "to": target_id, "changed": True, "event": ev}


def undo_switch() -> dict[str, Any]:
    s = get_strategy()
    if not s["previous_id"]:
        return {"from": s["current_id"], "to": s["current_id"], "changed": False}
    return switch_to(s["previous_id"], reason="undo", insert_card=False)


def check_quota(request_id: str | None = None) -> list[dict[str, Any]]:
    """每个轮询周期执行一次；预警与耗尽都用 state_flags 去重。"""
    s = get_strategy()
    created: list[dict[str, Any]] = []
    for v in get_providers_view():
        pid = v["id"]
        warn_key = f"warned:{pid}"
        exhausted_key = f"exhausted:{pid}"
        if v["unlimited"] or not v["enabled"] or v["limited"]:
            continue
        if 0 < v["remaining_pct"] <= round(s["threshold"] * 100):
            if not db.get_flag(warn_key):
                db.set_flag(warn_key)
                msg = f"{v['name']} 剩余 {v['remaining_pct']}%，低于阈值 {round(s['threshold'] * 100)}%"
                created.append(db.add_event("WARN", "threshold", msg, pid, request_id))
                emit("warning", "threshold", msg, request_id=request_id, provider_id=pid)
                log_event("WARN", "quota_warning", msg, request_id=request_id, model=pid,
                          prompt_version=settings.PROMPT_VERSION, latency=0, token_usage=0)
        else:
            db.clear_flag(warn_key)

        if v["remaining_pct"] <= 0:
            if not db.get_flag(exhausted_key):
                db.set_flag(exhausted_key)
                msg = f"{v['name']} 免费额度已耗尽（{v['usage_label']}）"
                created.append(db.add_event("ERROR", "exhausted", msg, pid, request_id))
                emit("error", "exhausted", msg, request_id=request_id, provider_id=pid)
            if pid == s["current_id"] and s["auto_switch"]:
                nxt = pick_next(pid)
                if nxt:
                    switch_to(nxt, "auto", request_id)
                    created.append(db.add_event("INFO", "failover",
                                               f"当前源耗尽，已自动切至 {nxt}，请求未中断", nxt, request_id))
                else:
                    created.append(db.add_event("WARN", "no_fallback",
                                               "全部数据源耗尽/限流，保持当前源避免抖动", pid, request_id))
        else:
            db.clear_flag(exhausted_key)
    return created


async def poll_once() -> None:
    rows = db.fetch_all("SELECT * FROM providers WHERE enabled = 1 ORDER BY id")
    for row in rows:
        try:
            if settings.PROVIDER_MODE == "http":
                used = await _http_usage.snapshot(row)
            else:
                used = _mock_usage.snapshot(row)
        except UpstreamFailure as exc:
            log_event("WARN", "usage_poll_failed", str(exc), provider_id=row["id"])
            continue
        db.execute("UPDATE providers SET used = ?, updated_at = ? WHERE id = ?",
                   (used, db.now_iso(), row["id"]))
        db.execute(
            "INSERT INTO quota_snapshots (provider_id,used,remaining,captured_at) VALUES (?,?,?,?)",
            (row["id"], used, max(0, row["quota_limit"] - used), db.now_iso()),
        )
    check_quota()
    emit("tick", "poll", "额度轮询完成", request_id=None)


async def poller_loop() -> None:
    while True:
        try:
            await poll_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 轮询失败按退避重试，不影响主链路
            log_event("ERROR", "poller_error", f"轮询异常：{type(exc).__name__}: {exc}")
            await asyncio.sleep(min(settings.POLL_INTERVAL_S * 3, 30))
        else:
            await asyncio.sleep(settings.POLL_INTERVAL_S)


def start_poller() -> None:
    global _poller_task
    if _poller_task is None or _poller_task.done():
        _poller_task = asyncio.create_task(poller_loop())


async def stop_poller() -> None:
    global _poller_task
    if _poller_task and not _poller_task.done():
        _poller_task.cancel()
        try:
            await _poller_task
        except asyncio.CancelledError:
            pass
    _poller_task = None


def poller_alive() -> bool:
    return bool(_poller_task and not _poller_task.done())


def simulate_429(request_id: str | None = None) -> dict[str, Any]:
    """注入一次 429：耗尽当前源 → 校验备胎 → 切换 → 重放成功。"""
    rid = request_id or "sim-" + uuid.uuid4().hex[:8]
    s = get_strategy()
    cur = db.fetch_one("SELECT * FROM providers WHERE id = ?", (s["current_id"],))
    assert cur is not None
    db.execute("UPDATE providers SET used = ?, updated_at = ? WHERE id = ?",
               (cur["quota_limit"], db.now_iso(), cur["id"]))
    db.add_event("ERROR", "http_429", f"429 quota_exceeded · {cur['name']} 今日免费额度耗尽",
                 cur["id"], rid)
    db.add_event("INFO", "validate_chain", "启动备胎链路校验", cur["id"], rid)
    db.set_flag(f"exhausted:{cur['id']}")
    nxt = pick_next(cur["id"])
    if not nxt:
        db.add_event("WARN", "no_fallback", "全部数据源耗尽/限流，等待人工切换", cur["id"], rid)
        return {"request_id": rid, "from_provider": cur["id"], "to_provider": cur["id"],
                "auto_switch": s["auto_switch"], "replay_status": 503,
                "message": "无可用备胎，未切换", "events": recent_events(6)}
    switched = s["auto_switch"]
    if switched:
        switch_to(nxt, "auto", rid)
    db.add_event("INFO", "replay", f"请求重放成功 200 OK · request_id={rid} · 上下文完整保留",
                 nxt, rid)
    db.add_event("INFO", "drill_done", "429 演练完成 · 请求未中断", nxt, rid)
    emit("drill", "429_done", "429 演练完成 · 请求未中断", request_id=rid,
         from_provider=cur["id"], to_provider=nxt)
    return {"request_id": rid, "from_provider": cur["id"], "to_provider": nxt,
            "auto_switch": switched, "replay_status": 200,
            "message": "429 → 自动切换 → 请求重放成功", "events": recent_events(8)}


def recent_events(limit: int = 30) -> list[dict[str, Any]]:
    return db.fetch_all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))


def trend_points() -> list[dict[str, Any]]:
    """24h 消耗趋势：由快照 + 固定 seed 生成确定性演示曲线（标注 DEMO）。"""
    import hashlib
    base = int(hashlib.sha256(settings.RANDOM_SEED.to_bytes(4, "little")).hexdigest()[:4], 16)
    return [{"hour": f"{h:02d}:00",
             "used": 180 + (base + h * 137) % 620 + (120 if 9 <= h <= 18 else 0)}
            for h in range(24)]


def kpi() -> dict[str, Any]:
    s = get_strategy()
    views = get_providers_view()
    cur = next((v for v in views if v["id"] == s["current_id"]), views[0])
    available = sum(1 for v in views if v["status"] not in {"exhausted", "limited"})
    logs = db.fetch_all("SELECT token_usage FROM request_logs")
    return {
        "current_model": cur["name"], "remaining_label": cur["usage_label"],
        "available_sources": available, "today_switches": s["switch_count"],
        "avoided_requests": s["avoided"],
        "today_tokens": sum(r["token_usage"] for r in logs),
    }
