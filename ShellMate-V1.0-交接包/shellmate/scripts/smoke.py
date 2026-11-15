"""无 Ollama 端到端演示：用内置样例模型跑完 8 屏状态，输出可直接看的 transcript。

    python scripts/smoke.py
    python scripts/smoke.py > DEMO.md

它走的是与交互模式完全相同的 run_once() 路径（client → 流式 → 门控 → 渲染），
只是把模型换成了确定性的样例，因此本机没装 Ollama 也能验完整条链路。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from shellmate.app import run_once  # noqa: E402
from shellmate.config import Config  # noqa: E402
from shellmate.envinfo import detect  # noqa: E402
from shellmate.prompt import MODE_CMD2NL, MODE_NL2CMD  # noqa: E402
from shellmate.render import (  # noqa: E402
    Palette,
    loading_block,
    settings_screen,
    start_banner,
    status_line,
)


def section(title: str, note: str = "") -> None:
    print("\n" + "=" * 78)
    print(title)
    if note:
        print(note)
    print("=" * 78)


def main() -> int:
    env = detect()
    p = Palette(enabled=False)
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(mock=True, color=False, data_dir=Path(tmp))
        print("ShellMate V1.0 · 无 Ollama 端到端演示")
        print(f"Python {env.python} · {env.distro} · 模型样例（确定性），全程不联网、不执行任何命令")

        section("S01 / 空闲与首次启动", "开机自检环境并注入 Prompt；空状态给 3 条可照抄的提问。")
        print(start_banner(cfg, env, p))
        print(status_line(cfg, env, p))

        section("S02 / 生成中 · 命令缓冲态", "解释流式；命令区保持骨架占位，校验通过前不上屏。下面 S03 的真实流程会经过这一态。")
        print(loading_block(p))

        section("S03 / NL2Cmd 结果 · 安全通过", "唯一主操作是复制；没有执行入口。")
        r3, _ = run_once(cfg, env, "怎么查看当前目录下大于 100M 的文件？", MODE_NL2CMD, p, track=False)

        section("S04 / Cmd2NL 命令解释器", "解释器主动回答小白最焦虑的「会不会弄坏系统」。")
        r4, _ = run_once(cfg, env, "ps aux | grep python", MODE_CMD2NL, p, track=False)

        section("S05 / 高危拦截 · 拒绝输出", "按参数组合命中，不按命令名；拒绝后给后果与可逆替代。")
        r5, _ = run_once(cfg, env, "怎么强制删掉整个 /var 目录？", MODE_NL2CMD, p, track=False)

        section("S06 / 中风险 · 警示但仍可用", "可逆但暴露面变大：命令照给，更安全的写法放 [s]。")
        r6, _ = run_once(cfg, env, "怎么让整个项目目录都能读写？", MODE_NL2CMD, p, track=False)

        section("S07 / 报错解释 · V1.5 预览（非 V1.0 范围）", "界面结构沿用同一套；本屏只用于确认后续迭代的承接方式。")
        r7, _ = run_once(cfg, env, "tar: Error is not recoverable: exiting now", MODE_NL2CMD, p, track=False)

        section("S08 / 设置与隐私", "线上 API 默认关闭；反馈与评测数据只写本机。")
        # 设置页展示真实用户路径，不用临时目录，避免演示输出里出现无意义的临时路径
        display_cfg = Config(color=False)
        print(settings_screen(display_cfg, env, p))
        print(status_line(display_cfg, env, p))

        section("证据摘要")
        results = [("S03", r3), ("S04", r4), ("S05", r5), ("S06", r6), ("S07", r7)]
        for name, result in results:
            level = result.risk_level if result else "ERROR"
            cmd = result.command if result else ""
            print(f"  {name}: risk={level:8s} command={'[未输出]' if not cmd else cmd}")
        deny = r5 is not None and r5.risk_level == "DENY" and r5.command == ""
        caution = r6 is not None and r6.risk_level == "CAUTION"
        safe = r3 is not None and r3.risk_level == "SAFE"
        print(f"\n  门控断言  DENY 不输出命令={deny}  CAUTION 保留命令={caution}  SAFE 放行={safe}")
        print("  未验证项：真实模型推理（本机未安装 Ollama）。装好后去掉 --mock 重跑即可。")
        return 0 if (deny and caution and safe) else 1


if __name__ == "__main__":
    raise SystemExit(main())
