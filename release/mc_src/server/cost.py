"""成本与节省估算。

SafeShell Pro 的核心卖点之一是「推理成本压到近零」：客户端走免费模型（如智谱
GLM-4-Flash / GLM-4.7-Flash 在免费档均为 0 元），但我们仍要能向团队管理员证明
「省了多少钱」。本模块只做一件事：把一次调用的 token 用量折算成「如果走付费等价模型
要花多少钱」，免费档自身计费恒为 0，二者之差即节省额。

所有价格均为可配置的参考价（CNY / 千 token），不是实时厂商报价；落地页与控制台
明确标注「参考价 · 仅供节省估算」。换计费口径 = 改 REFERENCE_PRICING。
"""
from __future__ import annotations

from typing import Any

# 付费等价模型的参考单价（人民币 / 千 token）。取主流付费小模型的 blended 中位值，
# 作为「若不用免费档，大致要花多少」的保守估算基准。
REFERENCE_PRICING: dict[str, float] = {
    "input_per_1k": 0.005,    # ¥0.005 / 1K 输入 token
    "output_per_1k": 0.015,   # ¥0.015 / 1K 输出 token
}


def estimate(tokens_in: int, tokens_out: int) -> dict[str, float]:
    """返回一次调用的成本结构。

    返回字段：
    - billed: 本次实付（免费档恒为 0）
    - paid_equivalent: 若走付费等价模型的应付金额
    - saved: 节省额（= paid_equivalent - billed，恒等于 paid_equivalent）
    """
    paid = (tokens_in / 1000.0) * REFERENCE_PRICING["input_per_1k"] + \
           (tokens_out / 1000.0) * REFERENCE_PRICING["output_per_1k"]
    paid = round(paid, 6)
    return {
        "billed": 0.0,            # 免费模型实付为 0
        "paid_equivalent": paid,
        "saved": paid,            # 节省 = 付费等价 - 实付
    }


def aggregate_saved(rows: list[dict[str, Any]]) -> dict[str, float]:
    """对审计明细做节省汇总。rows 含 prompt_tokens / completion_tokens。"""
    total_in = sum(int(r.get("prompt_tokens", 0) or 0) for r in rows)
    total_out = sum(int(r.get("completion_tokens", 0) or 0) for r in rows)
    return estimate(total_in, total_out)
