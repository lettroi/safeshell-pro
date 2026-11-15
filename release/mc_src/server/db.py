"""SQLite 持久化层（开发/演示运行时）。

生产路径的 MySQL DDL 见项目根目录 schema.sql（含复合索引与 utf8mb4）。
所有写操作是短事务；落库失败由调用方捕获并降级，不阻塞主响应。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import KNOWLEDGE_VERSION, ROOT, settings

_LOCK = threading.RLock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def db_path() -> Path:
    p = Path(settings.DB_PATH)
    if not p.is_absolute():
        p = ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    with _LOCK:
        conn = connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _LOCK:
        conn = connect()
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()


def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    with tx() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid or 0


SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  vendor TEXT NOT NULL,
  unit TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0,
  quota_limit INTEGER NOT NULL DEFAULT 0,
  reset_at TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  tier TEXT NOT NULL,
  free TEXT NOT NULL,
  rpm INTEGER NOT NULL DEFAULT 0,
  unlimited INTEGER NOT NULL DEFAULT 0,
  limited INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  key_hint TEXT NOT NULL DEFAULT 'sk-••••7f3a',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quota_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_id TEXT NOT NULL,
  used INTEGER NOT NULL,
  remaining INTEGER NOT NULL,
  captured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quota_provider_time ON quota_snapshots(provider_id, captured_at);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  level TEXT NOT NULL,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  provider_id TEXT,
  request_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
CREATE TABLE IF NOT EXISTS request_logs (
  request_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  user_id_hash TEXT NOT NULL,
  intent TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  temperature REAL NOT NULL,
  max_tokens INTEGER NOT NULL,
  timeout_ms INTEGER NOT NULL,
  retrieval_hits INTEGER NOT NULL DEFAULT 0,
  sources TEXT NOT NULL DEFAULT '[]',
  latency_ms INTEGER NOT NULL,
  ttft_ms INTEGER NOT NULL,
  token_usage INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  answer_digest TEXT,
  fallback_from TEXT,
  routed_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_request_user_created ON request_logs(user_id_hash, ts);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  request_count INTEGER NOT NULL DEFAULT 0,
  switch_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  model TEXT,
  request_id TEXT,
  sources TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_conv_created ON messages(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS knowledge_docs (
  doc_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  source TEXT NOT NULL,
  url TEXT NOT NULL,
  version TEXT NOT NULL,
  lang TEXT NOT NULL,
  tags TEXT NOT NULL,
  content TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  current_id TEXT NOT NULL,
  previous_id TEXT,
  auto_switch INTEGER NOT NULL DEFAULT 1,
  threshold REAL NOT NULL DEFAULT 0.10,
  chain TEXT NOT NULL,
  notify_in_app INTEGER NOT NULL DEFAULT 1,
  notify_email INTEGER NOT NULL DEFAULT 0,
  notify_webhook INTEGER NOT NULL DEFAULT 0,
  switch_count INTEGER NOT NULL DEFAULT 0,
  avoided INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state_flags (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
-- 真实厂商接入凭据：只存服务端本地，API 只回脱敏值，日志严禁写入 api_key
CREATE TABLE IF NOT EXISTS provider_credentials (
  provider_id TEXT PRIMARY KEY,
  api_base TEXT NOT NULL,
  model TEXT NOT NULL,
  api_key TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
-- SafeShell Pro：raw 透传端点每次调用的审计明细（可审计、可计费、可归因）
CREATE TABLE IF NOT EXISTS raw_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  client_id TEXT NOT NULL,
  model TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'unknown',
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  ok INTEGER NOT NULL DEFAULT 1,
  error_code TEXT,
  risk TEXT,
  command_hash TEXT,
  billed REAL NOT NULL DEFAULT 0,
  saved REAL NOT NULL DEFAULT 0,
  request_id TEXT,
  -- 防篡改哈希链：row_hash = sha256(prev_hash + 规范化后的本行内容)
  prev_hash TEXT,
  row_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_audit_ts ON raw_audit(ts DESC);
CREATE INDEX IF NOT EXISTS idx_raw_audit_client ON raw_audit(client_id, ts DESC);
-- SafeShell Pro：团队级安全门控策略（单一行，全团队共享；客户端可拉取后本地强制执行）
CREATE TABLE IF NOT EXISTS team_policy (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  deny_patterns TEXT NOT NULL DEFAULT '[]',
  risk_threshold TEXT NOT NULL DEFAULT 'DENY',
  allowed_models TEXT NOT NULL DEFAULT '[]',
  max_tokens INTEGER NOT NULL DEFAULT 512,
  enforce_local_gate INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  updated_by TEXT NOT NULL DEFAULT 'admin'
);
-- SafeShell Pro：免费模型启用状态。政策变化导致某模型不再免费时，管理员停用它；
-- 网关直接拒绝被停用模型的请求（硬开关），客户端下一次拉取模型清单时也会自动跳过。
CREATE TABLE IF NOT EXISTS model_status (
  model TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 1,
  disabled_at TEXT,
  reason TEXT,
  updated_at TEXT NOT NULL
);
-- SafeShell Pro：模型健康度熔断。免费模型过载（429/5xx/超时）时进入短时冷却，
-- 期间网关快速拒绝，让客户端立刻切到下一个候选，避免每次都白等上游超时（实测 60s）。
-- 与 model_status 的人工停用互补：这里只做临时自动熔断，冷却到期自动恢复。
CREATE TABLE IF NOT EXISTS model_health (
  model TEXT PRIMARY KEY,
  failures INTEGER NOT NULL DEFAULT 0,
  cooldown_until REAL NOT NULL DEFAULT 0,
  last_error TEXT,
  updated_at TEXT NOT NULL
);
"""

SEED_PROVIDERS: list[dict[str, Any]] = [
    {"id": "gemini", "name": "Gemini 2.0 Flash", "vendor": "Google", "unit": "RPD",
     "used": 1000, "quota_limit": 1000, "reset_at": "每日 16:00", "tier": "按日",
     "free": "1,000 次 / 日", "rpm": 15, "limited": 0,
     "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"},
    {"id": "qwen", "name": "Qwen2.5-72B", "vendor": "阿里云百炼", "unit": "tokens",
     "used": 710000, "quota_limit": 1000000, "reset_at": "每月 1 日", "tier": "按月",
     "free": "1M tokens / 月", "rpm": 60, "limited": 0,
     "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"},
    {"id": "deepseek", "name": "DeepSeek-V3", "vendor": "DeepSeek", "unit": "tokens",
     "used": 1760000, "quota_limit": 10000000, "reset_at": "每月 1 日", "tier": "按月",
     "free": "10M tokens / 月", "rpm": 60, "limited": 0,
     "endpoint": "https://api.deepseek.com/v1/chat/completions"},
    {"id": "llama", "name": "Llama-3.3-70B", "vendor": "Groq", "unit": "TPM",
     "used": 12400, "quota_limit": 30000, "reset_at": "每分钟", "tier": "按分钟",
     "free": "30K TPM", "rpm": 30, "limited": 0,
     "endpoint": "https://api.groq.com/openai/v1/chat/completions"},
    {"id": "glm", "name": "GLM-4-Flash", "vendor": "智谱", "unit": "免费",
     "used": 0, "quota_limit": 0, "reset_at": "—", "tier": "不限量",
     "free": "不限量 / 免费", "rpm": 30, "unlimited": 1, "limited": 0,
     "endpoint": "https://open.bigmodel.cn/api/paas/v4/chat/completions"},
    {"id": "mistral", "name": "Mistral Small", "vendor": "Mistral AI", "unit": "tokens",
     "used": 120000, "quota_limit": 1000000, "reset_at": "每月 1 日", "tier": "按月",
     "free": "1M tokens / 月", "rpm": 30, "limited": 0,
     "endpoint": "https://api.mistral.ai/v1/chat/completions"},
    {"id": "cohere", "name": "Command R", "vendor": "Cohere", "unit": "tokens",
     "used": 990000, "quota_limit": 1000000, "reset_at": "每月 1 日", "tier": "按月",
     "free": "1M tokens / 月", "rpm": 20, "limited": 1,
     "endpoint": "https://api.cohere.com/v2/chat"},
    {"id": "nvidia", "name": "Llama-3.1-Nemotron", "vendor": "NVIDIA NIM", "unit": "tokens",
     "used": 34000, "quota_limit": 500000, "reset_at": "每日 00:00", "tier": "按日",
     "free": "500K tokens / 日", "rpm": 40, "limited": 0,
     "endpoint": "https://integrate.api.nvidia.com/v1/chat/completions"},
    {"id": "siliconflow", "name": "Qwen2.5-7B", "vendor": "硅基流动", "unit": "tokens",
     "used": 88000, "quota_limit": 1000000, "reset_at": "每月 1 日", "tier": "按月",
     "free": "1M tokens / 月", "rpm": 60, "limited": 0,
     "endpoint": "https://api.siliconflow.cn/v1/chat/completions"},
]

SEED_CONVERSATIONS = [
    ("c1", "额度切换验证 · 14:32"),
    ("c2", "免费模型横向对比 · 昨天"),
    ("c3", "长文本摘要测试 · 11-10"),
]


def init_db() -> None:
    with tx() as conn:
        conn.executescript(SCHEMA)
        # 轻量迁移：旧库已存在 raw_audit 时 CREATE TABLE IF NOT EXISTS 不会补列。
        # 哈希链字段缺失会让 /audit/verify 报错，这里按列补齐（幂等）。
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(raw_audit)")}
        for col in ("prev_hash", "row_hash"):
            if col not in cols:
                conn.execute(f"ALTER TABLE raw_audit ADD COLUMN {col} TEXT")
        if conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0] == 0:
            ts = now_iso()
            for p in SEED_PROVIDERS:
                conn.execute(
                    """INSERT INTO providers
                       (id,name,vendor,unit,used,quota_limit,reset_at,endpoint,tier,free,rpm,
                        unlimited,limited,enabled,key_hint,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (p["id"], p["name"], p["vendor"], p["unit"], p["used"], p["quota_limit"],
                     p["reset_at"], p["endpoint"], p["tier"], p["free"], p["rpm"],
                     p.get("unlimited", 0), p.get("limited", 0), 1, "sk-••••7f3a", ts),
                )
            conn.execute(
                """INSERT INTO strategy
                   (id,current_id,previous_id,auto_switch,threshold,chain,notify_in_app,
                    notify_email,notify_webhook,switch_count,avoided,updated_at)
                   VALUES (1,?,?,?,?,?,?,?,?,?,?,?)""",
                ("deepseek", "gemini", int(settings.AUTO_SWITCH), settings.QUOTA_WARN_THRESHOLD,
                 settings.FALLBACK_CHAIN, int(settings.NOTIFY_IN_APP),
                 int(settings.NOTIFY_EMAIL), int(settings.NOTIFY_WEBHOOK), 3, 12, ts),
            )
            for cid, title in SEED_CONVERSATIONS:
                conn.execute(
                    """INSERT INTO conversations
                       (id,title,created_at,updated_at,total_tokens,request_count,switch_count)
                       VALUES (?,?,?,?,?,?,?)""",
                    (cid, title, ts, ts, 4812 if cid == "c1" else 0, 3 if cid == "c1" else 0,
                     1 if cid == "c1" else 0),
                )
            for role, content in [
                ("user", "Gemini 2.0 Flash 还有免费额度吗？耗尽后会自动切到哪家？"),
                ("system", "已自动切换模型 · 14:32:07：Gemini 2.0 Flash 当日 1,000 次免费调用用尽，"
                           "已切至 DeepSeek-V3，请求未中断（86ms）。上下文与历史消息完整保留，本次回答继续。"),
                ("assistant", "已切至 DeepSeek-V3。Gemini 2.0 Flash 的 1,000 RPD 免费额度于 14:32:07 用尽；"
                              "备胎链路按 deepseek → qwen → glm 优先级选择，上下文完整保留。"),
            ]:
                conn.execute(
                    """INSERT INTO messages
                       (conversation_id,role,content,model,request_id,sources,created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    ("c1", role, content, "deepseek", "seed-1432", "[]", ts),
                )
            conn.execute(
                """INSERT INTO events (ts,level,kind,message,provider_id,request_id)
                   VALUES (?,?,?,?,?,?)""",
                (ts, "ROUTE", "switch",
                 "14:32:07 Gemini 2.0 Flash 免费额度耗尽，自动切至 DeepSeek-V3，请求未中断（86ms）",
                 "gemini", "seed-1432"),
            )
            conn.execute(
                """INSERT INTO events (ts,level,kind,message,provider_id,request_id)
                   VALUES (?,?,?,?,?,?)""",
                (ts, "WARN", "threshold",
                 "Command R 剩余额度 1%，低于阈值 10%，但当前处于限流中，标记为「限流中」",
                 "cohere", None),
            )
            conn.execute(
                """INSERT INTO request_logs
                   (request_id,ts,user_id_hash,intent,model,prompt_version,temperature,max_tokens,
                    timeout_ms,retrieval_hits,sources,latency_ms,ttft_ms,token_usage,error_code,
                    answer_digest,fallback_from,routed_by)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("seed-1432", ts, "u_demo", "quota_qa", "deepseek", settings.PROMPT_VERSION,
                 settings.LLM_TEMPERATURE, settings.LLM_MAX_TOKENS, settings.LLM_TIMEOUT_MS,
                 2, json.dumps(["policy-gemini-flash", "strategy-chain-v1"]), 86, 14, 1284,
                 None, "已切至 DeepSeek-V3", "gemini", "auto_switch"),
            )
        if conn.execute("SELECT COUNT(*) FROM knowledge_docs WHERE version = ?",
                        (KNOWLEDGE_VERSION,)).fetchone()[0] == 0:
            from .knowledge import seed_documents

            for doc in seed_documents():
                # doc_id 是主键：同 id 的策略/运行手册条目会被新版本就地覆盖，
                # 已下线的旧政策条目保留但 version 不同，不会进入检索结果。
                conn.execute(
                    """INSERT OR REPLACE INTO knowledge_docs
                       (doc_id,title,source,url,version,lang,tags,content,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (doc["doc_id"], doc["title"], doc["source"], doc["url"], doc["version"],
                     doc["lang"], json.dumps(doc["tags"], ensure_ascii=False),
                     doc["content"], now_iso()),
                )


def add_event(level: str, kind: str, message: str, provider_id: str | None = None,
              request_id: str | None = None) -> dict[str, Any]:
    ts = now_iso()
    eid = execute(
        "INSERT INTO events (ts,level,kind,message,provider_id,request_id) VALUES (?,?,?,?,?,?)",
        (ts, level, kind, message, provider_id, request_id),
    )
    return {"id": eid, "ts": ts, "level": level, "kind": kind, "message": message,
            "provider_id": provider_id, "request_id": request_id}


def add_request_log(row: dict[str, Any]) -> None:
    execute(
        """INSERT OR REPLACE INTO request_logs
           (request_id,ts,user_id_hash,intent,model,prompt_version,temperature,max_tokens,
            timeout_ms,retrieval_hits,sources,latency_ms,ttft_ms,token_usage,error_code,
            answer_digest,fallback_from,routed_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (row["request_id"], row["ts"], row["user_id_hash"], row["intent"], row["model"],
         row["prompt_version"], row["temperature"], row["max_tokens"], row["timeout_ms"],
         row["retrieval_hits"], json.dumps(row.get("sources", []), ensure_ascii=False),
         row["latency_ms"], row["ttft_ms"], row["token_usage"], row.get("error_code"),
         row.get("answer_digest"), row.get("fallback_from"), row.get("routed_by")),
    )


def get_flag(key: str) -> bool:
    return fetch_one("SELECT value FROM state_flags WHERE key = ?", (key,)) is not None


def get_flag_value(key: str) -> str | None:
    row = fetch_one("SELECT value FROM state_flags WHERE key = ?", (key,))
    return str(row["value"]) if row else None


def set_flag(key: str, value: str = "1") -> None:
    execute(
        "INSERT OR REPLACE INTO state_flags (key,value,updated_at) VALUES (?,?,?)",
        (key, value, now_iso()),
    )


def clear_flag(key: str) -> None:
    execute("DELETE FROM state_flags WHERE key = ?", (key,))


def bump_counter(field: str, delta: int = 1) -> None:
    if field not in {"switch_count", "avoided"}:
        raise ValueError("unsupported counter")
    execute(f"UPDATE strategy SET {field} = {field} + ?, updated_at = ? WHERE id = 1",
            (delta, now_iso()))


def unix_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# SafeShell Pro：raw 透传审计（可审计 / 可计费 / 可归因）
# ---------------------------------------------------------------------------
# 默认停用的免费模型。2026-11-15：glm-4.7-flash 免费档长期 429 / 挂起 60s，
# 用户决定停用。模型仍保留在预设清单里，管理员可在控制台「启用」重新放开，
# 或在 model_status 表里显式覆盖；这里只影响「表里没有记录时」的默认值。
DEFAULT_DISABLED_MODELS: dict[str, str] = {
    "glm-4.7-flash": "智谱免费档长期 429 / 超时（错误码 1305），2026-11-15 起默认停用",
}


def _audit_digest(row: dict[str, Any], prev_hash: str) -> str:
    """规范化本行关键字段后做 sha256。只含不可变业务字段，不含自增 id。"""
    parts = [
        prev_hash, str(row["ts"]), str(row["client_id"]), str(row["model"]),
        str(row.get("mode", "unknown")), str(row.get("prompt_tokens", 0)),
        str(row.get("completion_tokens", 0)), str(row.get("total_tokens", 0)),
        str(row.get("latency_ms", 0)), str(int(bool(row.get("ok", True)))),
        str(row.get("error_code") or ""), str(row.get("risk") or ""),
        str(row.get("command_hash") or ""), str(row.get("billed", 0.0)),
        str(row.get("saved", 0.0)), str(row.get("request_id") or ""),
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def add_raw_audit(row: dict[str, Any]) -> None:
    """写入一条审计并续上哈希链。所有写入都经 _LOCK 串行，保证链顺序。"""
    with tx() as conn:
        last = conn.execute("SELECT row_hash FROM raw_audit ORDER BY id DESC LIMIT 1").fetchone()
        prev = (last["row_hash"] if last else "") or ""
        digest = _audit_digest(row, prev)
        conn.execute(
            """INSERT INTO raw_audit
               (ts,client_id,model,mode,prompt_tokens,completion_tokens,total_tokens,
                latency_ms,ok,error_code,risk,command_hash,billed,saved,request_id,
                prev_hash,row_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row["ts"], row["client_id"], row["model"], row.get("mode", "unknown"),
             row.get("prompt_tokens", 0), row.get("completion_tokens", 0), row.get("total_tokens", 0),
             row.get("latency_ms", 0), int(bool(row.get("ok", True))), row.get("error_code"),
             row.get("risk"), row.get("command_hash"), row.get("billed", 0.0), row.get("saved", 0.0),
             row.get("request_id"), prev, digest),
        )


def verify_audit_chain(limit: int | None = None) -> dict[str, Any]:
    """逐行重算哈希链，返回是否被篡改。limit 用于大库抽样（按 id 升序取最后 N 行）。"""
    with _LOCK:
        conn = connect()
        try:
            total = conn.execute("SELECT COUNT(*) c FROM raw_audit").fetchone()["c"]
            if limit and total > limit:
                rows = conn.execute(
                    "SELECT * FROM raw_audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
                rows = list(reversed(rows))
                # 抽样时第一行的 prev_hash 无法回溯，只校验本行 digest，不断链判定
                sampled = True
            else:
                rows = conn.execute("SELECT * FROM raw_audit ORDER BY id ASC").fetchall()
                sampled = False
            prev = ""
            checked = 0
            legacy = 0
            for r in rows:
                d = dict(r)
                if not d.get("row_hash"):
                    # 升级前写入的历史行没有哈希字段：跳过并如实计数，链从下一行重新起算，
                    # 绝不把「老数据」误报成「被篡改」。
                    legacy += 1
                    prev = ""
                    continue
                expected_prev = d.get("prev_hash") or ""
                if sampled and checked == 0:
                    prev = expected_prev  # 抽样起点：以上一行的记录值为准
                elif expected_prev != prev and checked > 0:
                    return {"ok": False, "checked": checked, "total": total, "legacy_rows": legacy,
                            "first_bad_id": d["id"], "message": "哈希链断裂：prev_hash 与上一行不匹配",
                            "sampled": sampled}
                canonical = {
                    "ts": d["ts"], "client_id": d["client_id"], "model": d["model"],
                    "mode": d["mode"], "prompt_tokens": d["prompt_tokens"],
                    "completion_tokens": d["completion_tokens"], "total_tokens": d["total_tokens"],
                    "latency_ms": d["latency_ms"], "ok": bool(d["ok"]),
                    "error_code": d["error_code"], "risk": d["risk"],
                    "command_hash": d["command_hash"], "billed": d["billed"],
                    "saved": d["saved"], "request_id": d["request_id"],
                }
                expected_hash = _audit_digest(canonical, expected_prev)
                if d.get("row_hash") != expected_hash:
                    return {"ok": False, "checked": checked, "total": total, "legacy_rows": legacy,
                            "first_bad_id": d["id"], "message": "审计行内容与哈希不符（疑似被篡改）",
                            "sampled": sampled}
                prev = expected_hash
                checked += 1
            msg = "哈希链完整，未发现篡改"
            if legacy:
                msg += f"；{legacy} 行为升级前的历史数据（无哈希字段，已跳过）"
            return {"ok": True, "checked": checked, "total": total, "legacy_rows": legacy,
                    "first_bad_id": None, "message": msg, "sampled": sampled}
        finally:
            conn.close()


def audit_export_rows(limit: int = 10000, client_id: str | None = None) -> list[dict[str, Any]]:
    if client_id:
        return fetch_all("SELECT * FROM raw_audit WHERE client_id=? ORDER BY id ASC LIMIT ?",
                         (client_id, limit))
    return fetch_all("SELECT * FROM raw_audit ORDER BY id ASC LIMIT ?", (limit,))


def model_latency_stats(since_minutes: int = 60) -> dict[str, dict[str, Any]]:
    """按模型聚合近期成功率与平均延迟，供候选链动态评分（P1-1）。"""
    cutoff = datetime.fromtimestamp(time.time() - since_minutes * 60, timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")
    rows = fetch_all(
        "SELECT model, ok, latency_ms FROM raw_audit WHERE ts >= ? AND latency_ms > 0",
        (cutoff,))
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        s = out.setdefault(r["model"], {"calls": 0, "ok": 0, "sum_latency": 0, "samples": 0})
        s["calls"] += 1
        s["ok"] += int(bool(r["ok"]))
        if r["latency_ms"]:
            s["sum_latency"] += int(r["latency_ms"])
            s["samples"] += 1
    for s in out.values():
        s["success_rate"] = round(s["ok"] / s["calls"], 3) if s["calls"] else 0.0
        s["avg_latency_ms"] = round(s["sum_latency"] / s["samples"]) if s["samples"] else 0
    return out


def recent_raw_audit(limit: int = 50, client_id: str | None = None) -> list[dict[str, Any]]:
    if client_id:
        return fetch_all(
            "SELECT * FROM raw_audit WHERE client_id = ? ORDER BY ts DESC LIMIT ?",
            (client_id, limit))
    return fetch_all("SELECT * FROM raw_audit ORDER BY ts DESC LIMIT ?", (limit,))


def raw_audit_report(client_id: str | None = None) -> dict[str, Any]:
    """聚合审计明细：调用数、token、延迟、节省额、按模型/模式拆解。"""
    rows = recent_raw_audit(100_000, client_id)  # 全量聚合（演示规模足够）
    total = len(rows)
    ok_rows = [r for r in rows if r["ok"]]
    err_rows = [r for r in rows if not r["ok"]]
    total_in = sum(int(r["prompt_tokens"] or 0) for r in rows)
    total_out = sum(int(r["completion_tokens"] or 0) for r in rows)
    total_tokens = sum(int(r["total_tokens"] or 0) for r in rows)
    latencies = [int(r["latency_ms"] or 0) for r in ok_rows if r["latency_ms"]]
    avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else 0.0

    saved = 0.0
    for r in rows:
        saved += float(r["saved"] or 0.0)

    by_model: dict[str, dict[str, Any]] = {}
    by_mode: dict[str, dict[str, Any]] = {}
    for r in rows:
        m = r["model"]
        bm = by_model.setdefault(m, {"model": m, "calls": 0, "tokens": 0, "saved": 0.0})
        bm["calls"] += 1
        bm["tokens"] += int(r["total_tokens"] or 0)
        bm["saved"] += float(r["saved"] or 0.0)
        md = r["mode"] or "unknown"
        bd = by_mode.setdefault(md, {"mode": md, "calls": 0, "ok": 0, "err": 0})
        bd["calls"] += 1
        if r["ok"]:
            bd["ok"] += 1
        else:
            bd["err"] += 1

    return {
        "total_calls": total,
        "ok_calls": len(ok_rows),
        "error_calls": len(err_rows),
        "total_tokens": total_tokens,
        "total_prompt_tokens": total_in,
        "total_completion_tokens": total_out,
        "avg_latency_ms": avg_latency,
        "estimated_saved_cny": round(saved, 4),
        "billed_cny": 0.0,
        "by_model": sorted(by_model.values(), key=lambda x: -x["calls"]),
        "by_mode": list(by_mode.values()),
        "client_id": client_id,
    }


# ---------------------------------------------------------------------------
# SafeShell Pro：团队级安全门控策略
# ---------------------------------------------------------------------------
def get_policy() -> dict[str, Any]:
    row = fetch_one("SELECT * FROM team_policy WHERE id = 1")
    if not row:
        ts = now_iso()
        execute(
            """INSERT INTO team_policy
               (id,deny_patterns,risk_threshold,allowed_models,max_tokens,
                enforce_local_gate,updated_at,updated_by)
               VALUES (1,'[]','DENY','[]',512,1,?,?)""",
            (ts, "admin"))
        return {
            "deny_patterns": [], "risk_threshold": "DENY", "allowed_models": [],
            "max_tokens": 512, "enforce_local_gate": True, "updated_at": ts, "updated_by": "admin",
        }
    import json as _json
    return {
        "deny_patterns": _json.loads(row["deny_patterns"] or "[]"),
        "risk_threshold": row["risk_threshold"],
        "allowed_models": _json.loads(row["allowed_models"] or "[]"),
        "max_tokens": row["max_tokens"],
        "enforce_local_gate": bool(row["enforce_local_gate"]),
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
    }


def set_policy(patch: dict[str, Any], updated_by: str = "admin") -> dict[str, Any]:
    cur = get_policy()
    merged = dict(cur)
    for k, v in patch.items():
        if k in {"deny_patterns", "risk_threshold", "allowed_models", "max_tokens",
                 "enforce_local_gate"}:
            merged[k] = v
    import json as _json
    ts = now_iso()
    execute(
        """INSERT OR REPLACE INTO team_policy
           (id,deny_patterns,risk_threshold,allowed_models,max_tokens,
            enforce_local_gate,updated_at,updated_by)
           VALUES (1,?,?,?,?,?,?,?)""",
        (_json.dumps(merged["deny_patterns"], ensure_ascii=False), merged["risk_threshold"],
         _json.dumps(merged["allowed_models"], ensure_ascii=False), merged["max_tokens"],
         int(bool(merged["enforce_local_gate"])), ts, updated_by))
    merged["updated_at"] = ts
    merged["updated_by"] = updated_by
    return merged


# ---------------------------------------------------------------------------
# SafeShell Pro：免费模型启用状态（政策变化不再免费时停用）
# ---------------------------------------------------------------------------
def get_model_status(model: str) -> bool:
    """返回模型是否启用。表里有记录以表为准；无记录时按 DEFAULT_DISABLED_MODELS 决定。"""
    row = fetch_one("SELECT enabled FROM model_status WHERE model = ?", (model,))
    return bool(row["enabled"]) if row else model not in DEFAULT_DISABLED_MODELS


def model_status_reason(model: str) -> str:
    row = fetch_one("SELECT enabled, reason FROM model_status WHERE model = ?", (model,))
    if row and not row["enabled"]:
        return row["reason"] or "管理员停用"
    if not row and model in DEFAULT_DISABLED_MODELS:
        return DEFAULT_DISABLED_MODELS[model]
    return ""


def set_model_status(model: str, enabled: bool, reason: str = "") -> dict[str, Any]:
    ts = now_iso()
    execute(
        """INSERT OR REPLACE INTO model_status (model,enabled,disabled_at,reason,updated_at)
           VALUES (?,?,?,?,?)""",
        (model, int(enabled), (ts if not enabled else None), reason, ts))
    # 人工重新启用时顺手清除熔断状态，避免重新免费后仍被冷却挡住
    if enabled:
        execute("DELETE FROM model_health WHERE model = ?", (model,))
    return {"model": model, "enabled": enabled, "reason": reason, "updated_at": ts}


# ---------------------------------------------------------------------------
# SafeShell Pro：模型健康度熔断（临时过载自动冷却，到期自动恢复）
# ---------------------------------------------------------------------------
MODEL_COOLDOWN_SECONDS = 300


def model_in_cooldown(model: str) -> bool:
    """冷却中 → 网关应快速拒绝，让客户端立即切换到下一个候选模型。"""
    row = fetch_one("SELECT cooldown_until FROM model_health WHERE model = ?", (model,))
    return bool(row and float(row["cooldown_until"]) > time.time())


def model_cooldown_remaining(model: str) -> int:
    row = fetch_one("SELECT cooldown_until FROM model_health WHERE model = ?", (model,))
    if not row:
        return 0
    return max(0, int(float(row["cooldown_until"]) - time.time()))


def mark_model_failure(model: str, error: str) -> None:
    """上游 429/5xx/超时：累计失败并刷新冷却窗口。"""
    execute(
        """INSERT INTO model_health (model,failures,cooldown_until,last_error,updated_at)
           VALUES (?,1,?,?,?)
           ON CONFLICT(model) DO UPDATE SET
             failures = failures + 1,
             cooldown_until = excluded.cooldown_until,
             last_error = excluded.last_error,
             updated_at = excluded.updated_at""",
        (model, time.time() + MODEL_COOLDOWN_SECONDS, str(error)[:64], now_iso()))


def mark_model_success(model: str) -> None:
    execute("DELETE FROM model_health WHERE model = ?", (model,))


def model_health_all() -> dict[str, dict[str, Any]]:
    """供 /api/v1/models 标注冷却状态（不回显密钥，不含敏感信息）。"""
    now = time.time()
    return {
        r["model"]: {"failures": r["failures"],
                     "cooldown_remaining": max(0, int(float(r["cooldown_until"]) - now)),
                     "last_error": r["last_error"]}
        for r in fetch_all("SELECT * FROM model_health")
    }

