"""免费 API 政策知识库：版本化的官方核实快照，离线可复现。

- 数据来自 server/data/free_tier_catalog.json，每条带真实官方 source_url、
  核实日期与 verification 级别；不再使用编造的 docs.local 占位链接。
- 这是**静态快照**，不是实时抓取：项目运行时不发起任何外部请求。
- metadata 过滤：lang=zh 且 version=KNOWLEDGE_VERSION；旧版本数据保留但不参与检索，
  便于按版本回溯「政策口径何时变化」。
- 回答中的 sources 必须经 validate_sources() 校验属于本次命中集合，防止编造来源。

注意：额度表（providers）与上游调用仍是 mock 演示数据；本文件只负责「政策问答」的知识来源。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .config import KNOWLEDGE_VERSION, settings
from .db import fetch_all

CATALOG_PATH = Path(__file__).resolve().parent / "data" / "free_tier_catalog.json"


def load_catalog() -> dict[str, Any]:
    """读取静态政策清单；文件缺失或损坏时启动即失败，不静默降级到旧数据。"""
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if not data.get("entries") or not data.get("catalog_version"):
        raise ValueError(f"政策清单格式非法：{CATALOG_PATH}")
    if data["catalog_version"] != KNOWLEDGE_VERSION:
        raise ValueError(
            f"清单版本 {data['catalog_version']} 与 KNOWLEDGE_VERSION {KNOWLEDGE_VERSION} 不一致"
        )
    return data


def catalog_meta() -> dict[str, Any]:
    cat = load_catalog()
    return {
        "catalog_version": cat["catalog_version"],
        "last_verified": cat["last_verified"],
        "entry_count": len(cat["entries"]),
        "official_count": sum(1 for e in cat["entries"]
                              if e["verification"] in {"official_docs", "official_pdf"}),
        "scope_note": cat["scope_note"],
    }


def seed_documents() -> list[dict[str, Any]]:
    """把清单展开为 knowledge_docs 行；url / verification 均可核验。"""
    cat = load_catalog()
    return [
        {
            "doc_id": e["doc_id"],
            "title": e["title"],
            "source": f"{e['provider']}｜{e['verification']}",
            "url": e["source_url"],
            "version": KNOWLEDGE_VERSION,
            "lang": "zh",
            "tags": e["tags"],
            "content": e["content"],
        }
        for e in cat["entries"]
    ]


# 高区分度词：命中权重 1.0；泛词权重 0.3
HIGH_WEIGHT_TAGS = {
    "gemini", "google", "ai studio", "flash", "groq", "gpt-oss", "openrouter", "credits",
    "cohere", "command", "trial", "cloudflare", "workers ai", "文本生成",
    "智谱", "bigmodel", "glm", "阿里云", "百炼", "qwen", "新人免费额度", "用完即停",
    "腾讯云", "tokenhub", "混元", "nvidia", "nim", "mistral", "la plateforme",
    "变更", "取消", "下线", "2026", "限流", "按项目", "数据条款", "429",
    "切换", "备胎", "策略", "阈值", "自动切换", "重放", "quota_exceeded",
    "密钥", "api key", "脱敏", "单位", "归一化", "request_id",
}


def search(query: str, top_k: int | None = None, min_score: float | None = None) -> list[dict[str, Any]]:
    """关键词 + metadata 过滤检索。返回按分数降序的 hits；无命中返回空列表。"""
    k = top_k if top_k is not None else settings.RETRIEVAL_TOP_K
    threshold = min_score if min_score is not None else settings.RETRIEVAL_MIN_SCORE
    q = query.lower().strip().replace("切到", "切换").replace("换到", "切换")
    rows = fetch_all(
        "SELECT * FROM knowledge_docs WHERE lang = ? AND version = ?",
        ("zh", KNOWLEDGE_VERSION),
    )
    hits: list[dict[str, Any]] = []
    for row in rows:
        tags = json.loads(row["tags"])
        matched = [t for t in tags if t.lower() in q]
        if not matched:
            continue
        weight = sum(1.0 if t.lower() in HIGH_WEIGHT_TAGS else 0.3 for t in matched)
        title_bonus = (0.2 if any(w in HIGH_WEIGHT_TAGS and w in q
                                  for w in row["title"].lower().split()) else 0.0)
        score = 0.35 + weight / math.sqrt(len(tags)) + title_bonus
        if score >= threshold:
            hits.append({
                "doc_id": row["doc_id"], "title": row["title"], "source": row["source"],
                "url": row["url"], "chunk_id": row["doc_id"], "version": row["version"],
                "score": round(score, 4), "snippet": row["content"],
            })
    hits.sort(key=lambda h: (-h["score"], h["doc_id"]))
    return hits[:k]


def validate_sources(sources: list[str], hits: list[dict[str, Any]]) -> list[str]:
    """引用必须来自本次检索命中集合，防止模型编造来源。"""
    allowed = {h["doc_id"] for h in hits}
    return [s for s in sources if s in allowed]
