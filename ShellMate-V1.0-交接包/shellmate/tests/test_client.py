"""模型客户端：双协议解析、流式不泄漏命令、non-thinking、mock 闭环。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from shellmate.client import (
    CommandResult,
    ModelClient,
    _LineProtocolParser,
    mock_response,
    parse_json_protocol,
    parse_line_protocol,
)
from shellmate.config import Config
from shellmate.envinfo import detect
from shellmate.errors import ParseError
from shellmate.prompt import MODE_NL2CMD

SAMPLE = """EXPLAIN: 只读地查找大文件，不会改动内容。
---
COMMAND: find . -type f -size +100M
RISK: SAFE
REASON: 只读取文件信息
PARAMS: find=查找命令; . -type f -size=参数; +100M=大于 100MB"""


class TestLineProtocol(unittest.TestCase):
    def test_parse_ok(self):
        r = parse_line_protocol(SAMPLE)
        self.assertEqual(r.command, "find . -type f -size +100M")
        self.assertEqual(r.risk_level, "SAFE")
        self.assertEqual(r.model_risk, "SAFE")
        self.assertEqual(len(r.params), 3)
        self.assertIn("只读", r.explain)

    def test_missing_field_raises(self):
        with self.assertRaises(ParseError):
            parse_line_protocol("EXPLAIN: 只有解释\n---\nCOMMAND: ls")

    def test_bad_risk_raises(self):
        bad = SAMPLE.replace("RISK: SAFE", "RISK: MAYBE")
        with self.assertRaises(ParseError):
            parse_line_protocol(bad)

    def test_markdown_fence_rejected(self):
        bad = SAMPLE.replace("find . -type f -size +100M", "```find . ```")
        with self.assertRaises(ParseError):
            parse_line_protocol(bad)

    def test_json_protocol(self):
        payload = {
            "explain": "列出文件",
            "command": "ls -la",
            "risk_level": "SAFE",
            "reason": "只读",
            "params": [{"token": "ls", "meaning": "列出目录"}],
        }
        r = parse_json_protocol(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(r.command, "ls -la")
        self.assertEqual(r.params[0].meaning, "列出目录")

    def test_json_bad_raises(self):
        with self.assertRaises(ParseError):
            parse_json_protocol("{not json")


class TestStreamingDoesNotLeakCommand(unittest.TestCase):
    def test_feed_never_returns_command_text(self):
        parser = _LineProtocolParser()
        emitted = []
        for i in range(0, len(SAMPLE), 7):
            piece = parser.feed(SAMPLE[i : i + 7])
            if piece:
                emitted.append(piece)
        emitted.append(parser.finalize())
        text = "".join(emitted)
        self.assertIn("只读地查找大文件", text)
        self.assertNotIn("find . -type f -size", text)
        self.assertNotIn("COMMAND", text)

    def test_mock_stream_events(self):
        cfg = Config(mock=True, color=False)
        client = ModelClient(cfg, detect())
        events = list(client.stream("怎么查看当前目录下大于 100M 的文件？", MODE_NL2CMD))
        kinds = [e.kind for e in events]
        self.assertIn("explain_delta", kinds)
        self.assertIn("buffered", kinds)
        self.assertEqual(kinds[-1], "done")
        explain = "".join(e.text for e in events if e.kind == "explain_delta")
        self.assertNotIn("find . -type f -size +100M", explain)
        self.assertEqual(events[-1].result.command, "find . -type f -size +100M")


class TestPayload(unittest.TestCase):
    def test_non_thinking_forced(self):
        cfg = Config(color=False)
        payload = ModelClient(cfg, detect())._payload("查看文件", MODE_NL2CMD, None)
        self.assertIs(payload["think"], False)
        self.assertIs(payload["stream"], True)
        self.assertIn("num_ctx", payload["options"])
        self.assertNotIn("format", payload)

    def test_json_format_adds_schema(self):
        cfg = Config(color=False, response_format="json")
        payload = ModelClient(cfg, detect())._payload("查看文件", MODE_NL2CMD, None)
        self.assertIn("format", payload)
        self.assertEqual(payload["format"]["type"], "object")

    def test_history_is_injected(self):
        cfg = Config(color=False)
        payload = ModelClient(cfg, detect())._payload("再看一遍", MODE_NL2CMD, "上一轮问过大文件")
        self.assertIn("上一轮", payload["prompt"])

    def test_mock_deny_case(self):
        raw = mock_response("怎么强制删掉整个 /var 目录？", MODE_NL2CMD)
        self.assertEqual(parse_line_protocol(raw).risk_level, "DENY")


if __name__ == "__main__":
    unittest.main()
