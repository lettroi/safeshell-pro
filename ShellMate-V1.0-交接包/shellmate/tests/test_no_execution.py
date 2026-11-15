"""安全边界审计：源码里不存在任何执行用户命令的路径。

这是 V1.0 最硬的一条约束，用静态扫描防止后续改动悄悄加回执行能力。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
SOURCE = ROOT / "src" / "shellmate"

BANNED = (
    "os.system(",
    "subprocess",
    "Popen(",
    "shell=True",
    "eval(",
    "exec(",
    "os.popen",
    "commands.getoutput",
)


class TestNoExecutionPath(unittest.TestCase):
    def test_no_execution_primitives_in_source(self):
        files = sorted(SOURCE.glob("*.py"))
        self.assertTrue(files)
        for path in files:
            text = path.read_text(encoding="utf-8")
            for token in BANNED:
                with self.subTest(file=path.name, token=token):
                    self.assertNotIn(token, text)

    def test_constant_line_exists_in_source(self):
        text = (SOURCE / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("只生成 · 不执行 · 不隔离", text)

    def test_gate_clears_deny_command(self):
        text = (SOURCE / "gate.py").read_text(encoding="utf-8")
        self.assertIn('result.command = ""', text)


if __name__ == "__main__":
    unittest.main()
