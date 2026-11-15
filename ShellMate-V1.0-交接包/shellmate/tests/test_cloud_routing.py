"""SafeShell Pro 客户端：多候选模型路由 + 团队策略本地强制执行。

全部离线：HTTP 通过 mock urllib.request.urlopen 模拟，不发起任何真实请求。
"""
from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from shellmate.client import ModelClient, StreamEvent
from shellmate.config import Config
from shellmate.envinfo import EnvInfo
from shellmate.policy import (TeamPolicy, apply_team_policy, candidate_models,
                              fetch_policy, _derive)
from shellmate.prompt import MODE_CMD2NL, MODE_NL2CMD

GOOD = """EXPLAIN: 递归查找当前目录下大于 100MB 的普通文件，只读操作。
---
COMMAND: find . -type f -size +100M
RISK: SAFE
REASON: 只读取文件信息，不写入不删除
PARAMS: find=查找命令; -type f=只看普通文件; -size +100M=大于 100MB"""

# 实测中多个开源模型会漏掉 EXPLAIN / 分隔符，直接解析会抛 ParseError
BAD = "COMMAND: du -h | grep 100M\nRISK: SAFE\nREASON: 只读\nPARAMS: du=查看磁盘"


def _env() -> EnvInfo:
    return EnvInfo(system="Linux", distro="Ubuntu 24.04", shell="bash",
                   shell_version="5.2", cwd="/home/tester", wsl=False,
                   wsl_distro="", gpu="none", python="3.13")


def _cfg(**kw) -> Config:
    base = dict(mock=False, color=False, policy_fetch=False,
                cloud_endpoint="http://127.0.0.1:8772/api/v1/raw/completions")
    base.update(kw)
    return Config(**base)


def _reply(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}], "usage": {}}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


def _events(client: ModelClient, mode: str = MODE_NL2CMD):
    return list(client.stream("查看大于100M的文件", mode))


class TestDerive(unittest.TestCase):
    def test_derive_policy_and_models_url(self):
        ep = "http://127.0.0.1:8772/api/v1/raw/completions"
        self.assertEqual(_derive(ep, "/policy"), "http://127.0.0.1:8772/api/v1/policy")
        self.assertEqual(_derive(ep, "/models"), "http://127.0.0.1:8772/api/v1/models")


class TestCandidateModels(unittest.TestCase):
    def test_disabled_and_whitelist_filtered(self):
        chain = ["a", "b", "c", "a"]
        p = TeamPolicy(disabled_models=["b"], allowed_models=["a", "c"])
        self.assertEqual(candidate_models(chain, p), ["a", "c"])

    def test_empty_whitelist_means_no_restriction(self):
        self.assertEqual(candidate_models(["a", "b"], TeamPolicy()), ["a", "b"])

    def test_all_disabled_yields_empty(self):
        self.assertEqual(candidate_models(["a"], TeamPolicy(disabled_models=["a"])), [])

    def test_fetch_policy_fails_silently(self):
        with patch("shellmate.policy._get_json", side_effect=OSError("offline")):
            p = fetch_policy("http://127.0.0.1:9/api/v1/raw/completions", timeout=0.1)
        self.assertEqual(p.source, "default")
        self.assertEqual(p.deny_patterns, [])


class TestApplyTeamPolicy(unittest.TestCase):
    def _result(self):
        from shellmate.client import parse_line_protocol
        from shellmate.gate import apply_gate
        return apply_gate(parse_line_protocol(GOOD))

    def test_deny_pattern_clears_command(self):
        p = TeamPolicy(deny_patterns=[r"\brm\s+-rf\b", r"^find\b"], source="gateway")
        r = apply_team_policy(self._result(), p)
        self.assertEqual(r.risk_level, "DENY")
        self.assertEqual(r.command, "")
        self.assertIn("TEAM-DENY-PATTERN", r.matched_rules)

    def test_risk_threshold_blocks_caution(self):
        from shellmate.client import CommandResult
        r = CommandResult(command="chmod 777 x", risk_level="CAUTION", reason="x")
        p = TeamPolicy(risk_threshold="SAFE", source="gateway")
        out = apply_team_policy(r, p)
        self.assertEqual(out.risk_level, "DENY")
        self.assertEqual(out.command, "")

    def test_default_policy_does_not_widen(self):
        r = self._result()
        before = (r.risk_level, r.command)
        self.assertEqual((apply_team_policy(r, TeamPolicy()).risk_level,
                          apply_team_policy(r, TeamPolicy()).command), before)

    def test_invalid_regex_is_skipped(self):
        p = TeamPolicy(deny_patterns=["[", r"^find\b"], source="gateway")
        r = apply_team_policy(self._result(), p)
        self.assertEqual(r.risk_level, "DENY")  # 第二条仍然生效


class TestCloudCandidateChain(unittest.TestCase):
    """模拟真实痛点：首选模型输出不合规行协议 / 被停用 → 自动切下一个候选。"""

    def _client(self, cfg=None):
        return ModelClient(cfg or _cfg(), _env())

    def test_parse_failure_switches_to_next_model(self):
        cfg = _cfg(cloud_models_deep="m1,m2", cloud_model_deep="m1",
                   cloud_model="glm-4-flash", cloud_models_light="m1")
        client = self._client(cfg)
        replies = iter([_reply(BAD), _reply(GOOD)])
        with patch("shellmate.client.urllib.request.urlopen",
                   side_effect=lambda *a, **k: _Resp(json.dumps(next(replies)).encode())):
            evs = list(client._stream_cloud("q", MODE_NL2CMD, None, force=True))
        done = [e for e in evs if e.kind == "done"]
        notices = [e for e in evs if e.kind == "notice"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0].result.cloud_model, "m2")
        self.assertTrue(done[0].result.from_cloud)
        self.assertEqual(len(notices), 1)
        self.assertIn("m2", notices[0].text)

    def test_disabled_model_is_skipped_before_request(self):
        cfg = _cfg(cloud_models_deep="off,on", cloud_model_deep="off",
                   cloud_model="glm-4-flash", cloud_models_light="on")
        client = self._client(cfg)
        client.team_policy = TeamPolicy(disabled_models=["off"])
        sent: list[str] = []

        def _fake(req, **kw):
            sent.append(json.loads(req.data.decode())["model"])
            return _Resp(json.dumps(_reply(GOOD)).encode())

        with patch("shellmate.client.urllib.request.urlopen", side_effect=_fake):
            # policy_fetch=False 会在 _stream_cloud 里覆盖 team_policy，改用 patch
            with patch("shellmate.policy.fetch_policy",
                       return_value=TeamPolicy(disabled_models=["off"], source="gateway")):
                cfg2 = _cfg(cloud_models_deep="off,on", cloud_model_deep="off",
                            cloud_model="glm-4-flash", cloud_models_light="on",
                            policy_fetch=True)
                client2 = ModelClient(cfg2, _env())
                evs = list(client2._stream_cloud("q", MODE_NL2CMD, None, force=True))
        self.assertEqual(sent, ["on"])
        self.assertEqual([e for e in evs if e.kind == "done"][0].result.cloud_model, "on")

    def test_http_403_falls_through_to_next(self):
        import urllib.error
        cfg = _cfg(cloud_models_deep="off,on", cloud_model_deep="off",
                   cloud_model="glm-4-flash", cloud_models_light="on")

        def _fake(req, **kw):
            model = json.loads(req.data.decode())["model"]
            if model == "off":
                raise urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b""))
            return _Resp(json.dumps(_reply(GOOD)).encode())

        with patch("shellmate.client.urllib.request.urlopen", side_effect=_fake):
            evs = list(ModelClient(cfg, _env())._stream_cloud("q", MODE_NL2CMD, None, force=True))
        self.assertEqual([e for e in evs if e.kind == "done"][0].result.cloud_model, "on")

    def test_all_candidates_exhausted_raises(self):
        cfg = _cfg(cloud_models_deep="m1,m2", cloud_model_deep="m1",
                   cloud_model="glm-4-flash", cloud_models_light="m1")
        with patch("shellmate.client.urllib.request.urlopen",
                   side_effect=lambda *a, **k: _Resp(json.dumps(_reply(BAD)).encode())):
            with self.assertRaises(Exception):
                list(ModelClient(cfg, _env())._stream_cloud("q", MODE_NL2CMD, None, force=True))

    def test_explicit_single_model_takes_priority(self):
        cfg = _cfg(cloud_models_deep="a,b", cloud_model_deep="Z",
                   cloud_model="glm-4-flash", cloud_models_light="a")
        sent: list[str] = []

        def _fake(req, **kw):
            sent.append(json.loads(req.data.decode())["model"])
            return _Resp(json.dumps(_reply(GOOD)).encode())

        with patch("shellmate.client.urllib.request.urlopen", side_effect=_fake):
            list(ModelClient(cfg, _env())._stream_cloud("q", MODE_NL2CMD, None, force=True))
        self.assertEqual(sent[0], "Z")


if __name__ == "__main__":
    unittest.main()
