"""渲染层：DENY 不输出命令、常量行常驻、复制不执行。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from shellmate import CONSTANT_LINE
from shellmate.client import CommandResult
from shellmate.config import Config
from shellmate.envinfo import detect
from shellmate.gate import apply_gate
from shellmate.render import (
    Palette,
    copy_command,
    result_block,
    settings_screen,
    start_banner,
    status_line,
    syntax_highlight,
)


class TestDenyRendering(unittest.TestCase):
    def test_deny_block_never_contains_command(self):
        result = apply_gate(CommandResult(command="rm -rf /var", risk_level="SAFE"))
        p = Palette(enabled=False)
        block = result_block(result, p)
        self.assertNotIn("rm -rf /var", block)
        self.assertIn("已拒绝生成命令", block)
        self.assertIn("du -sh /var/*", block)  # 给可逆替代，而不是把人推开
        self.assertNotIn("请联系管理员", block)

    def test_caution_block_shows_command_and_safer_way(self):
        result = apply_gate(CommandResult(command="chmod -R 777 ./project", risk_level="SAFE"))
        block = result_block(result, Palette(enabled=False))
        self.assertIn("chmod -R 777 ./project", block)
        self.assertIn("chmod -R 755 ./project", block)

    def test_safe_block_shows_command_and_copy_only(self):
        result = apply_gate(CommandResult(command="find . -type f -size +100M", risk_level="SAFE"))
        block = result_block(result, Palette(enabled=False))
        self.assertIn("find . -type f -size +100M", block)
        self.assertIn("[c] 复制到剪贴板", block)
        self.assertNotIn("执行此命令", block)


class TestChrome(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(color=False)
        self.env = detect()
        self.p = Palette(enabled=False)

    def test_constant_line_always_present(self):
        self.assertIn(CONSTANT_LINE, status_line(self.cfg, self.env, self.p))
        self.assertIn(CONSTANT_LINE, start_banner(self.cfg, self.env, self.p) + status_line(self.cfg, self.env, self.p))

    def test_cloud_flag_is_visible(self):
        cloud = self.cfg.with_changes(cloud_enabled=True, cloud_endpoint="https://example.com/v1")
        self.assertIn("线上 · 数据会离开本机", status_line(cloud, self.env, self.p))
        self.assertIn("已开启", settings_screen(cloud, self.env, self.p))

    def test_settings_mentions_local_feedback_path(self):
        self.assertIn(str(self.cfg.feedback_path), settings_screen(self.cfg, self.env, self.p))

    def test_syntax_highlight_without_color_is_lossless(self):
        cmd = "tar -xzf archive.tar.gz -C ./target"
        self.assertEqual(syntax_highlight(cmd, self.p), cmd)

    def test_copy_uses_osc52_only(self):
        out = copy_command("ls -la", self.p)
        self.assertIn("\x1b]52;c;", out)
        self.assertIn("不会执行", out)

    def test_copy_refused_when_empty(self):
        self.assertIn("没有可复制的命令", copy_command("", self.p))


if __name__ == "__main__":
    unittest.main()
