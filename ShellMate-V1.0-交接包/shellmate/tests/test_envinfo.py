"""环境探测与本地反馈：只读、不联网、写本机。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from shellmate.config import Config
from shellmate.envinfo import detect
from shellmate.client import CommandResult
from shellmate.feedback import record, stats


class TestEnvInfo(unittest.TestCase):
    def test_detect_has_required_fields(self):
        env = detect()
        self.assertTrue(env.system)
        self.assertTrue(env.distro)
        self.assertTrue(env.shell)
        self.assertTrue(env.cwd)
        self.assertTrue(env.python)

    def test_prompt_block_declares_bash_syntax(self):
        block = detect().prompt_block()
        self.assertIn("bash", block)
        self.assertIn("shell", block)
        self.assertIn("工作目录", block)

    def test_display_line_is_single_line(self):
        self.assertNotIn("\n", detect().display_line())


class TestFeedback(unittest.TestCase):
    def test_record_and_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(data_dir=Path(tmp))
            result = CommandResult(command="ls -la", risk_level="SAFE")
            record(cfg, "列出文件", result, "NL2Cmd", useful=True)
            record(cfg, "删除 /var", CommandResult(command="", risk_level="DENY"), "NL2Cmd", useful=False)
            data = stats(cfg)
            self.assertEqual(data["total"], 2)
            self.assertEqual(data["useful"], 1)
            self.assertEqual(data["not_useful"], 1)
            self.assertEqual(data["blocked"], 1)
            self.assertEqual(data["usefulness_rate"], 0.5)
            self.assertTrue(cfg.feedback_path.exists())

    def test_deny_feedback_never_stores_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(data_dir=Path(tmp))
            record(cfg, "删掉 /var", CommandResult(command="", risk_level="DENY"), "NL2Cmd")
            text = cfg.feedback_path.read_text(encoding="utf-8")
            self.assertNotIn("rm -rf", text)

    def test_missing_file_returns_zero_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = stats(Config(data_dir=Path(tmp) / "nested"))
            self.assertEqual(data["total"], 0)
            self.assertIsNone(data["usefulness_rate"])

    def test_env_var_is_not_leaked_into_prompt_by_default(self):
        self.assertFalse(Config().cloud_enabled)


if __name__ == "__main__":
    unittest.main()
