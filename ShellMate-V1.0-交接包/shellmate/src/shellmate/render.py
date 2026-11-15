"""终端渲染：解释流式、命令缓冲、整行呈现、语法分色、常量行。

这里只负责「把文字画出来」。任何函数都不执行命令、不推导安全结论。
风险等级由 gate.py 给出后才会调用 result_block()。
"""

from __future__ import annotations

import base64
import os
import sys
from typing import Iterable

from . import CONSTANT_LINE, __version__
from .client import CommandResult
from .config import Config
from .envinfo import EnvInfo

RGB = {
    "brand": (158, 255, 0),
    "safe": (61, 220, 151),
    "info": (88, 166, 255),
    "caution": (245, 165, 36),
    "danger": (255, 92, 92),
    "primary": (233, 238, 243),
    "secondary": (139, 148, 158),
    "muted": (107, 116, 128),
}


def enable_windows_vt() -> None:
    """Windows 10+ 终端默认关闭 VT 转义，这里开启以便显示 24-bit 颜色。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass  # 不支持就走 --no-color 降级


class Palette:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def paint(self, text: str, name: str, bold: bool = False) -> str:
        if not self.enabled or not text:
            return text
        r, g, b = RGB[name]
        prefix = f"\x1b[38;2;{r};{g};{b}m"
        if bold:
            prefix += "\x1b[1m"
        return f"{prefix}{text}\x1b[0m"

    def brand(self, t: str, bold: bool = False) -> str:
        return self.paint(t, "brand", bold)

    def safe(self, t: str, bold: bool = False) -> str:
        return self.paint(t, "safe", bold)

    def info(self, t: str, bold: bool = False) -> str:
        return self.paint(t, "info", bold)

    def caution(self, t: str, bold: bool = False) -> str:
        return self.paint(t, "caution", bold)

    def danger(self, t: str, bold: bool = False) -> str:
        return self.paint(t, "danger", bold)

    def muted(self, t: str) -> str:
        return self.paint(t, "muted")

    def primary(self, t: str, bold: bool = False) -> str:
        return self.paint(t, "primary", bold)

    def secondary(self, t: str) -> str:
        return self.paint(t, "secondary")


def syntax_highlight(command: str, p: Palette) -> str:
    """命令名白 / 选项蓝 / 路径琥珀 / 字符串绿 / 管道符琥珀。"""
    out: list[str] = []
    for i, tok in enumerate(command.split(" ")):
        if not tok:
            out.append(tok)
        elif i == 0:
            out.append(p.primary(tok, bold=True))
        elif tok in {"|", "||", "&&", ";", ">", ">>", "<"}:
            out.append(p.caution(tok, bold=True))
        elif tok.startswith("--") or (tok.startswith("-") and len(tok) > 1):
            out.append(p.info(tok))
        elif tok.startswith(("'", '"')) or tok.startswith("$"):
            out.append(p.safe(tok))
        elif "/" in tok or tok in {".", "..", "~"}:
            out.append(p.caution(tok))
        else:
            out.append(p.primary(tok))
    return " ".join(out)


# ---------------------------------------------------------------------------
# 屏幕构件
# ---------------------------------------------------------------------------

def start_banner(cfg: Config, env: EnvInfo, p: Palette) -> str:
    lines = [
        p.brand(f"shellmate v{__version__}   |   {cfg.model} · {cfg.quant}   |   本机推理", bold=True),
        p.secondary(env.display_line()),
        p.muted("以上信息仅注入本地 Prompt，不会离开这台机器。"),
        p.muted("─" * 64),
        p.primary("第一次用？直接问就行，例如："),
        p.info(" 1  怎么找出当前目录下大于 100M 的文件？"),
        p.info(" 2  ps aux | grep python 这句是什么意思？"),
        p.info(" 3  tar 怎么把 .tar.gz 解压到指定目录？"),
        p.muted("模式：NL2Cmd（自然语言转命令）· 敲 :mode 切到 Cmd2NL · :settings 设置 · :q 退出"),
    ]
    return "\n".join(lines)


def loading_block(p: Palette) -> str:
    """命令区骨架占位：校验完成前，命令行一个字都不上屏。"""
    return "\n".join([
        p.caution("命令区 · 正在做静态风险校验，暂不上屏"),
        p.muted("▒" * 46),
        p.muted("解释可以逐字出现，命令必须整行通过校验后再显示。"),
    ])


def _params_lines(result: CommandResult, p: Palette) -> str:
    if not result.params:
        return ""
    body = "  ".join(f"{item.token}  {item.meaning}" for item in result.params)
    return p.secondary(body)


def _safe_block(result: CommandResult, p: Palette) -> list[str]:
    label = "✓ 静态校验通过 · 只读" + (" · 未验证命令" if result.unverified else " · 已验证命令")
    lines = [
        p.safe(label),
        syntax_highlight(result.command, p),
        p.secondary("[c] 复制到剪贴板      [y] 有用      [n] 没用      [Tab] 查看参数拆解"),
    ]
    if result.params:
        lines.append(_params_lines(result, p))
    lines.append(p.muted("复制后请自行粘贴到终端执行；本工具不会替你运行任何命令。"))
    return lines


def _caution_block(result: CommandResult, p: Palette) -> list[str]:
    lines = [
        p.caution("▲ CAUTION  需确认 · " + result.reason, bold=True),
        p.secondary("命中规则：" + ", ".join(result.matched_rules)),
        p.caution("命令 · 已生成，带风险标记"),
        syntax_highlight(result.command, p),
        p.secondary("[s] 查看更安全写法      [c] 仍要复制（剪贴板内会附带风险注释）"),
    ]
    if result.consequences:
        lines.append(p.secondary("后果：" + result.consequences))
    if result.alternatives:
        lines.append(p.safe("更稳妥：" + result.alternatives[0]))
    lines.append(p.muted("本工具只生成、不执行、不隔离任何命令。"))
    return lines


def _deny_block(result: CommandResult, p: Palette) -> list[str]:
    lines = [
        p.danger("■ 已拒绝生成命令 · 该操作不可逆", bold=True),
        p.danger("命中规则：" + ", ".join(result.matched_rules)),
        p.primary("本工具只生成、不执行、不隔离任何命令。下面是后果说明与更安全的做法。"),
    ]
    if result.consequences:
        lines.append(p.secondary("后果：" + result.consequences))
    if result.alternatives:
        lines.append(p.safe("如果只是想清理磁盘，先看看是什么占了空间："))
        lines.append(p.primary(result.alternatives[0], bold=True))
        for alt in result.alternatives[1:]:
            lines.append(p.muted("· " + alt))
    lines.append(p.muted("命令文本未输出，也没有被写入剪贴板。"))
    return lines


def result_block(result: CommandResult, p: Palette, show_params: bool = True) -> str:
    if result.risk_level == "DENY":
        lines = _deny_block(result, p)
    elif result.risk_level == "CAUTION":
        lines = _caution_block(result, p)
    else:
        lines = _safe_block(result, p)
    if not show_params and result.params and result.risk_level == "SAFE":
        lines = [line for line in lines if line != _params_lines(result, p)]
    return "\n".join(lines)


def status_line(cfg: Config, env: EnvInfo, p: Palette, extra: str = "", from_cloud: bool = False, cloud_model: str = "") -> str:
    parts = [CONSTANT_LINE]
    if cfg.cloud_ready():
        parts.append("线上 · 数据会离开本机")
    elif from_cloud:
        parts.append("● 回退云端 · 数据离开本机")
    else:
        parts.append("本地 · 离线")
    if cloud_model:
        parts.append(cloud_model)
    else:
        parts.append("mock 模型" if cfg.mock else cfg.model)
    if env.wsl:
        parts.append("WSL2")
    parts.append("反馈仅存本机")
    if extra:
        parts.append(extra)
    return p.muted("     |     ".join(parts))


def error_block(err: Exception, p: Palette) -> str:
    render = getattr(err, "render", None)
    text = render() if callable(render) else str(err)
    return "\n".join(p.danger(line) if i == 0 else p.caution(line) for i, line in enumerate(text.splitlines()))


def settings_screen(cfg: Config, env: EnvInfo, p: Palette) -> str:
    lines = [
        p.brand("设置 / SETTINGS     ·     [Esc] 返回", bold=True),
        p.muted("推理位置 / PRIVACY"),
        p.safe("●  本机 Ollama          已启用 · 默认 · 数据不出本机"),
        (
            p.caution("●  线上开源模型 API     已开启 · 提问会离开本机")
            if cfg.cloud_ready()
            else p.muted("○  线上开源模型 API     关闭 · 开启需显式输入 enable-cloud 并二次确认")
        ),
        p.muted("模型 / MODEL"),
        p.primary(f"{cfg.model}  ·  {cfg.quant}  ·  Apache-2.0"),
        p.secondary("强制 non-thinking（锁定，避免思考链击穿响应时间目标）· 上下文仅保留上一轮"),
        p.muted("数据与评测 / LOCAL DATA"),
        p.secondary(f"反馈  {cfg.feedback_path}   ·   只写本机，不上传"),
        p.secondary("50 条本地测试集 · 命令可用率 / 幻觉率 统计脚本随 V1.0 一起交付"),
        p.muted(f"输出协议  {cfg.response_format}    彩色  {'开' if cfg.color else '关'}    Python {env.python}"),
    ]
    return "\n".join(lines)


def copy_command(command: str, p: Palette) -> str:
    """只复制，绝不执行。

    用 OSC 52 终端转义把文本送进剪贴板——纯输出、零子进程，
    不调用 shell，也不把命令交给任何外部程序作为指令执行。
    不支持 OSC 52 的终端会退化成「请手动选中复制」。
    """
    if not command:
        return p.danger("没有可复制的命令（高风险请求已被拒绝）。")
    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    osc = f"\x1b]52;c;{encoded}\x07"
    return (
        osc
        + p.safe("已发送复制指令到剪贴板")
        + "\n"
        + p.muted("若终端不支持 OSC 52，请选中上面的命令行手动复制；本工具不会执行它。")
    )


def write_stream(text: str) -> None:
    """解释文本逐字上屏。"""
    sys.stdout.write(text)
    sys.stdout.flush()


def write_lines(lines: Iterable[str]) -> None:
    for line in lines:
        print(line)
