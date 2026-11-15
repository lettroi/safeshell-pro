"""ShellMate 主循环：键盘优先、只生成不执行、状态常驻。

交互规则对应设计稿 8 屏：
S01 启动横幅 / S02 生成中命令缓冲 / S03 安全结果 / S04 解释器 /
S05 高危拒绝 / S06 中风险警示 / S07 报错解释(V1.5，不在本版) / S08 设置与隐私。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Sequence

from . import __version__
from .client import CommandResult, ModelClient
from .config import Config, load_config
from .envinfo import EnvInfo, detect
from .errors import ParseError
from .feedback import record, stats
from .gate import apply_gate
from .prompt import MODE_CMD2NL, MODE_NL2CMD
from .render import (
    Palette,
    copy_command,
    enable_windows_vt,
    error_block,
    loading_block,
    result_block,
    settings_screen,
    start_banner,
    status_line,
    syntax_highlight,
    write_stream,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shellmate",
        description="本地命令行助手：自然语言转命令 / 命令解释 / 静态风险提示。只生成，不执行。",
    )
    parser.add_argument("--mock", action="store_true", help="用内置样例模型跑通闭环，不需要 Ollama")
    parser.add_argument("--no-color", action="store_true", help="关闭彩色输出")
    parser.add_argument("--format", choices=["lines", "json"], help="输出协议（默认 lines，解释可流式）")
    parser.add_argument("--model", help="覆盖模型名")
    parser.add_argument("--host", help="覆盖 Ollama 地址")
    parser.add_argument("--once", metavar="问题", help="只跑一次并退出（脚本化验证用）")
    parser.add_argument("--mode", choices=["nl2cmd", "cmd2nl"], default="nl2cmd")
    parser.add_argument("--settings", action="store_true", help="打印设置页后退出")
    parser.add_argument("--stats", action="store_true", help="打印本机反馈统计后退出")
    parser.add_argument("--version", action="version", version=f"shellmate {__version__}")
    return parser


def read_key() -> str:
    """读单个按键（Tab / Esc / y / n / c / s / q）。零依赖，只用标准库。

    非交互环境（重定向、CI、脚本）返回空串，由调用方降级。
    """
    if not sys.stdin.isatty():
        return ""
    if os.name == "nt":
        try:
            import msvcrt

            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):  # 方向键等功能键，吃掉第二个字节
                msvcrt.getwch()
                return ""
            return ch
        except Exception:
            return ""
    try:
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        return ""


def _mode_from_arg(value: str) -> str:
    return MODE_CMD2NL if value == "cmd2nl" else MODE_NL2CMD


def run_once(
    cfg: Config,
    env: EnvInfo,
    query: str,
    mode: str,
    p: Palette,
    history: str | None = None,
    track: bool = True,
) -> tuple[CommandResult | None, int]:
    """跑完一次「提问 → 流式解释 → 缓冲 → 门控 → 整行呈现」。"""
    client = ModelClient(cfg, env)
    started = time.perf_counter()
    print(p.brand("> ") + p.primary(query))
    if cfg.cloud_ready():
        print(p.caution("● 线上模式 · 本次提问会离开本机"))
    else:
        print(p.muted(f"环境已注入：{env.distro} / {env.shell}  ·  模型 {cfg.model} (non-thinking)"))
    explain_label = (
        "解释 · 流式输出中"
        if cfg.response_format == "lines"
        else "解释 · 严格 JSON 模式（不流式）"
    )
    print(p.safe(explain_label))

    result: CommandResult | None = None
    wrote_explain = False
    placeholder = False
    using_cloud = False

    for event in client.stream(query, mode, history):
        if event.kind == "notice":
            print(p.caution(event.text))
            using_cloud = True
            continue
        if event.kind == "explain_delta":
            write_stream(event.text)
            wrote_explain = True
        elif event.kind == "buffered" and not placeholder:
            if wrote_explain:
                write_stream("\n")
                wrote_explain = False
            print(loading_block(p))
            placeholder = True
        elif event.kind == "done":
            if wrote_explain:
                write_stream("\n")
            result = event.result
        elif event.kind == "error" and event.error is not None:
            if wrote_explain:
                write_stream("\n")
            print(error_block(event.error, p))
            print(status_line(cfg, env, p))
            return None, 2

    if result is None:
        print(error_block(ParseError("模型没有返回完整结果"), p))
        return None, 2

    result = apply_gate(result)
    # 团队策略（云端模式从网关拉取，本地模式为空策略）：在确定性门控之后叠加，
    # 只收紧不放宽；DENY 时同样清空命令，确保任何渲染路径都不会输出被禁命令。
    tp = client.team_policy
    if tp is not None:
        from .policy import apply_team_policy
        result = apply_team_policy(result, tp)
    print(result_block(result, p))
    elapsed = time.perf_counter() - started
    print(status_line(cfg, env, p, f"耗时 {elapsed:.2f}s", from_cloud=using_cloud,
                      cloud_model=result.cloud_model if result else ""))
    if track:
        record(cfg, query, result, mode)
    return result, 0


def _handle_command(query: str, cfg: Config, env: EnvInfo, p: Palette) -> tuple[Config, bool]:
    """处理冒号命令与云开关。返回 (新配置, 是否要退出)。"""
    if query in {":q", "q", "quit", "exit"}:
        return cfg, True
    if query == ":settings":
        print(settings_screen(cfg, env, p))
        return cfg, False
    if query == ":stats":
        data = stats(cfg)
        print(p.primary("本机反馈统计"))
        for key in ("total", "useful", "not_useful", "unrated", "blocked", "unverified", "usefulness_rate", "path"):
            print(p.secondary(f"  {key}: {data[key]}"))
        return cfg, False
    if query == "disable-cloud":
        print(p.safe("已切回本机推理 · 数据不出本机"))
        return cfg.with_changes(cloud_enabled=False), False
    if query.startswith("enable-cloud"):
        endpoint = query.partition(" ")[2].strip() or os.environ.get("SHELLMATE_CLOUD_ENDPOINT", "")
        if not endpoint:
            print(p.caution("还缺线上端点：先设置 SHELLMATE_CLOUD_ENDPOINT，或用 enable-cloud <端点> 直接给。"))
            print(p.muted("本地模式不受影响，全部功能照常可用。"))
            return cfg, False
        confirm = input(p.caution("开启后提问会离开本机。再输入一遍 enable-cloud 确认：")).strip()
        if confirm != "enable-cloud":
            print(p.muted("已取消，仍为本机推理。"))
            return cfg, False
        print(p.caution("● 已开启线上体验模式 · 界面将常驻联网标识"))
        return cfg.with_changes(cloud_enabled=True, cloud_endpoint=endpoint), False
    return cfg, False


def interactive(cfg: Config, env: EnvInfo, p: Palette, initial_mode: str) -> int:
    print(start_banner(cfg, env, p))
    print(status_line(cfg, env, p))
    if env.is_windows_native():
        print(p.caution("检测到 Windows 原生终端：建议在 WSL2 的 bash 里使用，生成的命令按 bash 语法给出。"))
    mode = initial_mode
    history: str | None = None
    exit_code = 0

    while True:
        try:
            query = input(p.brand("> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not query:
            continue
        cfg, should_exit = _handle_command(query, cfg, env, p)
        if should_exit:
            break
        if query in {":settings", ":stats", "disable-cloud"} or query.startswith("enable-cloud"):
            continue
        if query == ":mode":
            mode = MODE_CMD2NL if mode == MODE_NL2CMD else MODE_NL2CMD
            print(p.brand(f"已切到 {mode}"))
            continue

        result, code = run_once(cfg, env, query, mode, p, history, track=False)
        exit_code = exit_code or code
        if result is None:
            continue
        history = f"{query} → {result.command or '[已拒绝输出]'}"
        print(p.muted("[c] 复制  [y] 有用  [n] 没用  [s] 更安全写法  [Tab] 参数拆解  [q] 退出"))
        key = read_key()
        useful: bool | None = None
        if key.lower() == "y":
            useful = True
            print(p.safe("已记录：有用（只存本机）"))
        elif key.lower() == "n":
            useful = False
            print(p.caution("已记录：没用（只存本机）"))
        elif key.lower() == "c":
            print(copy_command(result.command, p))
        elif key.lower() == "s":
            print(p.safe("更稳妥：" + (result.alternatives[0] if result.alternatives else "当前没有额外替代方案")))
        elif key == "\t":
            for item in result.params:
                print(p.secondary(f"  {item.token}  →  {item.meaning}"))
        elif key.lower() == "q" or key == "\x1b":
            record(cfg, query, result, mode, useful)
            break
        record(cfg, query, result, mode, useful)

    print(status_line(cfg, env, p))
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()
    if args.mock or os.environ.get("SHELLMATE_MOCK"):
        cfg = cfg.with_changes(mock=True)
    if args.no_color:
        cfg = cfg.with_changes(color=False)
    if args.format:
        cfg = cfg.with_changes(response_format=args.format)
    if args.model:
        cfg = cfg.with_changes(model=args.model)
    if args.host:
        cfg = cfg.with_changes(host=args.host.rstrip("/"))
    if cfg.color and sys.stdout.isatty():
        enable_windows_vt()

    env = detect()
    p = Palette(cfg.color)
    mode = _mode_from_arg(args.mode)

    if args.settings:
        print(settings_screen(cfg, env, p))
        return 0
    if args.stats:
        data = stats(cfg)
        print(f"反馈 {data['total']} 条 · 有用 {data['useful']} · 没用 {data['not_useful']} · 已拦截 {data['blocked']}")
        print(f"命令可用率 {data['usefulness_rate']}  ·  本机路径 {data['path']}")
        return 0
    if args.once:
        _, code = run_once(cfg, env, args.once, mode, p)
        return code
    return interactive(cfg, env, p, mode)
