"""fallback 逻辑：本地 Ollama 不可达且开启 cloud_fallback 时，应回退到云端。

仅验证路由与事件语义（notice 提示 + from_cloud 标记 + 解析成功）；
真实云端质量由接入后跑 --live 验证，不属于本测试范围。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from shellmate.client import CommandResult, ModelClient, StreamEvent
from shellmate.config import Config
from shellmate.errors import ModelUnavailableError


def _fake_cloud(self, query, mode, history, force=False):
    result = CommandResult(
        command="ls -la", risk_level="SAFE", reason="x", explain="ok", params=[]
    )
    result.from_cloud = True
    yield StreamEvent(kind="explain_delta", text="ok")
    yield StreamEvent(kind="done", result=result)


class FallbackTest(unittest.TestCase):
    def test_fallback_triggered_when_ollama_down(self):
        cfg = Config(
            mock=False,
            cloud_enabled=False,
            cloud_fallback=True,
            cloud_endpoint="http://127.0.0.1:8000/api/v1/raw/completions",
        )
        client = ModelClient(cfg, env=None)
        with patch.object(ModelClient, "_stream_ollama", side_effect=ModelUnavailableError("down")), \
             patch.object(ModelClient, "_stream_cloud", _fake_cloud):
            events = list(client.stream("怎么查看大于 100M 的文件？", "nl2cmd"))
        kinds = [e.kind for e in events]
        self.assertIn("notice", kinds)
        self.assertIn("done", kinds)
        done = next(e for e in events if e.kind == "done")
        self.assertTrue(done.result.from_cloud)
        self.assertEqual(done.result.command, "ls -la")

    def test_no_fallback_when_switch_off(self):
        cfg = Config(mock=False, cloud_fallback=False, cloud_endpoint="http://x")
        client = ModelClient(cfg, env=None)
        with patch.object(ModelClient, "_stream_ollama", side_effect=ModelUnavailableError("down")), \
             patch.object(ModelClient, "_stream_cloud", _fake_cloud):
            events = list(client.stream("q", "nl2cmd"))
        self.assertNotIn("notice", [e.kind for e in events])
        self.assertTrue(any(e.kind == "error" for e in events))

    def test_no_fallback_when_endpoint_missing(self):
        cfg = Config(mock=False, cloud_fallback=True, cloud_endpoint="")
        client = ModelClient(cfg, env=None)
        with patch.object(ModelClient, "_stream_ollama", side_effect=ModelUnavailableError("down")):
            events = list(client.stream("q", "nl2cmd"))
        self.assertNotIn("notice", [e.kind for e in events])
        self.assertTrue(any(e.kind == "error" for e in events))


if __name__ == "__main__":
    unittest.main()
