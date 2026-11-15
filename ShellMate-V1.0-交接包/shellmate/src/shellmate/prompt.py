"""Prompt 与输出协议。

两条铁律：
1. 强制 non-thinking。Qwen3 的思考链会把响应时间拖到 5 秒目标之外。
2. 模型填的 RISK 只是「参考意见」，最终风险等级由 gate.py 的参数级规则覆盖。
   绝不把安全判断交给模型。
"""

from __future__ import annotations

from typing import Any

from .envinfo import EnvInfo

MODE_NL2CMD = "NL2Cmd"
MODE_CMD2NL = "Cmd2NL"

LINE_PROTOCOL = """EXPLAIN: <一句中文白话解释，不超过两行，不要换行>
---
COMMAND: <只写一行命令，不要代码块、不要反引号、不要前后缀说明>
RISK: <SAFE 或 CAUTION 或 DENY，你的初步判断>
REASON: <一句话说明为什么是这个风险等级>
PARAMS: <片段>=<含义>; <片段>=<含义>"""

JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "explain": {"type": "string"},
        "command": {"type": "string"},
        "risk_level": {"type": "string", "enum": ["SAFE", "CAUTION", "DENY"]},
        "reason": {"type": "string"},
        "params": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"token": {"type": "string"}, "meaning": {"type": "string"}},
                "required": ["token", "meaning"],
            },
        },
    },
    "required": ["explain", "command", "risk_level", "reason", "params"],
}


def build_system_prompt(env: EnvInfo, response_format: str = "lines") -> str:
    head = """你是一个耐心的 Linux 命令行老师，面向完全不懂命令行的初学者。

当前用户环境（生成命令时必须遵守）：
{env}

硬性要求：
- 只输出一行可直接复制的命令，不要 Markdown 代码块、不要反引号、不要客套话。
- 用中文写白话解释，让没用过终端的人也能听懂；顺带说清这条命令会不会改动文件。
- 只使用真实存在的命令和参数，绝不编造选项。不确定时给最保守的只读命令。
- 不做任何执行动作；你只负责写出命令文本。
- 不输出思考过程、不输出分析步骤、不输出「让我想想」。
- 用户问危险操作时，不要说教，给一个更安全的只读命令作为替代。
""".format(env=env.prompt_block())

    if response_format == "json":
        return head + "\n只输出一个 JSON 对象，字段：explain / command / risk_level / reason / params（数组，每项含 token 与 meaning）。不要任何额外文字。"
    return head + "\n严格按下面的格式输出，每行一个字段，不要加任何其他内容：\n" + LINE_PROTOCOL


def build_user_prompt(query: str, mode: str, history: str | None = None) -> str:
    """V1.0 上下文最多保留上一轮（config.context_rounds = 1）。"""
    parts: list[str] = []
    if history:
        parts.append(f"上一轮（仅供参考，不要沿用其中的路径）：{history}\n")
    if mode == MODE_CMD2NL:
        parts.append(
            "任务：解释下面这条已有命令。COMMAND 原样抄回这条命令，"
            "EXPLAIN 用中文说明它整体做什么、会不会改动系统，PARAMS 逐段拆解。\n"
            f"命令：{query}"
        )
    else:
        parts.append(
            "任务：把下面的自然语言需求转成一条命令。若需求涉及不可逆操作，"
            "仍要给一条更安全的只读命令作为替代，不要给破坏性命令。\n"
            f"需求：{query}"
        )
    return "\n".join(parts)
