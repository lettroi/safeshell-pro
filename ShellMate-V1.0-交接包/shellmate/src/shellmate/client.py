"""模型客户端：Ollama 流式调用、双协议解析、mock 与 cloud 体验模式。

零第三方依赖，只用 urllib。
关键设计：
- 行协议下 EXPLAIN 段可增量上屏；一旦进入 --- 之后的 COMMAND 段，全部缓冲，
  解析 + 门控通过后由 render 层整行呈现。流式与安全门控因此不打架。
- JSON 协议下严格 schema，但解释不流式（这是可预期的取舍）。
- 本机没装 Ollama 时可用 mock 跑完整个交互闭环并做自动化测试。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator

from . import policy as team_policy
from .config import Config
from .envinfo import EnvInfo
from .errors import CloudDisabledError, ModelTimeoutError, ModelUnavailableError, ParseError
from .prompt import (
    JSON_SCHEMA,
    MODE_CMD2NL,
    MODE_NL2CMD,
    build_system_prompt,
    build_user_prompt,
)

RISK_LEVELS = {"SAFE", "CAUTION", "DENY"}

# 云端兜底错误专用话术（避免显示 Ollama 那套「本机没装模型」的指引）
_CLOUD_ERR = {"prefix": "云端模型暂不可用", "hint": ModelUnavailableError.CLOUD_HINT}

# 本客户端只与本地服务（Ollama / 本地网关）通信。Windows 上常存在 HTTPS_PROXY 等
# 环境变量，urllib 默认会把 127.0.0.1 / WSL 子网也路由到该代理；一旦代理离线就会
# 出现「积极拒绝 (os error 10061)」。这里对本地地址强制不走代理，远程网关仍走默认
# （尊重系统代理），兼顾本地优先与将来可能的远程网关。
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _open(req: "urllib.request.Request", timeout: float):
    import urllib.parse

    host = urllib.parse.urlparse(req.full_url).hostname or ""
    is_local = (
        host in ("127.0.0.1", "localhost", "::1")
        or host.startswith("127.")
        or host.startswith("172.")
        or host.startswith("192.168.")
        or host.startswith("10.")
        or host.startswith("[::1]")
    )
    if is_local:
        return _NO_PROXY_OPENER.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


@dataclass
class Param:
    token: str
    meaning: str


@dataclass
class CommandResult:
    """与交接规格一致的结构化结果。

    risk_level 初始值来自模型（model_risk 留痕），随后由 gate.py 覆盖为确定性结论。
    """

    command: str = ""
    risk_level: str = "SAFE"
    reason: str = ""
    explain: str = ""
    params: list[Param] = field(default_factory=list)
    model_risk: str = ""
    unverified: bool = False
    matched_rules: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    consequences: str = ""
    from_cloud: bool = False
    cloud_model: str = ""
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "explain": self.explain,
            "params": [{"token": p.token, "meaning": p.meaning} for p in self.params],
            "model_risk": self.model_risk,
            "unverified": self.unverified,
            "matched_rules": self.matched_rules,
            "from_cloud": self.from_cloud,
        }


@dataclass
class StreamEvent:
    """kind: explain_delta | buffered | done | error"""

    kind: str
    text: str = ""
    result: CommandResult | None = None
    error: Exception | None = None


class _LineProtocolParser:
    """增量提取 EXPLAIN 段；COMMAND 段一律不返回、只累积。"""

    SEP = "---"

    def __init__(self) -> None:
        self.raw = ""
        self.emitted = ""
        self.sealed = False  # 已进入 COMMAND 段

    def feed(self, delta: str) -> str:
        self.raw += delta
        if self.sealed:
            return ""
        idx = self.raw.find("EXPLAIN:")
        if idx < 0:
            return ""
        body = self.raw[idx + len("EXPLAIN:") :]
        sep = body.find(self.SEP)
        if sep >= 0:
            body = body[:sep]
            self.sealed = True
        else:
            # 扣住尾部，避免跨 chunk 的协议标记被当成解释显示出来
            body = body[:-6] if len(body) > 6 else ""
        body = body.lstrip()
        if not body.startswith(self.emitted):
            # 模型输出与已上屏内容不一致 → 停止增量，交给最终解析
            self.sealed = True
            return ""
        new = body[len(self.emitted) :]
        self.emitted = body
        return new

    def finalize(self) -> str:
        """补齐因尾部保留而压住的解释文字（仍不返回命令内容）。"""
        idx = self.raw.find("EXPLAIN:")
        if idx < 0:
            return ""
        body = self.raw[idx + len("EXPLAIN:") :]
        sep = body.find(self.SEP)
        if sep >= 0:
            body = body[:sep]
        body = body.lstrip("\n").rstrip()
        if body.startswith(self.emitted):
            new = body[len(self.emitted) :]
            self.emitted = body
            return new
        return ""


def parse_line_protocol(raw: str) -> CommandResult:
    """严格解析行协议。任何必需字段缺失/非法都抛 ParseError，绝不猜测补齐。"""
    text = raw.strip()
    fields: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "---":
            current = None
            continue
        known = False
        for key in ("EXPLAIN", "COMMAND", "RISK", "REASON", "PARAMS"):
            prefix = key + ":"
            if stripped.startswith(prefix):
                fields[key] = stripped[len(prefix) :].strip()
                current = key
                known = True
                break
        if not known and current and fields.get(current):
            fields[current] += " " + stripped

    for required in ("EXPLAIN", "COMMAND", "RISK", "REASON", "PARAMS"):
        if not fields.get(required):
            raise ParseError(f"缺少 {required} 字段")

    raw_command = fields["COMMAND"].strip()
    # 先查代码块标记，再做反引号容错；顺序反了会把 ``` 当作包裹符剥掉，绕过校验
    if "```" in raw_command:
        raise ParseError("命令里混入了 Markdown 代码块")
    command = raw_command.strip("`").strip()
    if not command or "\n" in command:
        raise ParseError("命令行不是单行文本")

    risk = fields["RISK"].strip().upper()
    if risk not in RISK_LEVELS:
        raise ParseError(f"RISK 值非法：{risk}")

    params: list[Param] = []
    for chunk in fields["PARAMS"].split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" in chunk:
            token, meaning = chunk.split("=", 1)
            params.append(Param(token.strip(), meaning.strip()))

    return CommandResult(
        command=command,
        risk_level=risk,
        model_risk=risk,
        reason=fields["REASON"],
        explain=fields["EXPLAIN"],
        params=params,
        raw=raw,
    )


def parse_json_protocol(raw: str) -> CommandResult:
    """严格 JSON schema 解析；失败同样抛 ParseError。"""
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise ParseError(f"不是合法 JSON：{exc.msg}") from exc
    if not isinstance(data, dict):
        raise ParseError("JSON 顶层不是对象")
    for key in ("command", "risk_level", "explain", "reason", "params"):
        if key not in data:
            raise ParseError(f"缺少 {key} 字段")
    command = str(data["command"]).strip().strip("`")
    risk = str(data["risk_level"]).strip().upper()
    if not command or "\n" in command:
        raise ParseError("命令行不是单行文本")
    if risk not in RISK_LEVELS:
        raise ParseError(f"risk_level 值非法：{risk}")
    if not isinstance(data["params"], list):
        raise ParseError("params 不是数组")
    params = []
    for item in data["params"]:
        if isinstance(item, dict) and "token" in item and "meaning" in item:
            params.append(Param(str(item["token"]), str(item["meaning"])))
    return CommandResult(
        command=command,
        risk_level=risk,
        model_risk=risk,
        reason=str(data["reason"]),
        explain=str(data["explain"]),
        params=params,
        raw=raw,
    )


# --------------------------------------------------------------------------
# mock：本机没有 Ollama 时，用确定性样例跑通完整交互闭环（也是测试基础设施）
# --------------------------------------------------------------------------

_MOCK_CASES: list[tuple[tuple[str, ...], str]] = [
    (
        ("100m", "大于 100", "大文件"),
        """EXPLAIN: 从当前目录递归查找普通文件，只列出体积大于 100MB 的。这是只读操作，不会改动任何文件。
---
COMMAND: find . -type f -size +100M
RISK: SAFE
REASON: 只读取文件信息，不写入、不删除
PARAMS: find=查找命令; .=从当前目录开始递归; -type f=只看普通文件; -size +100M=大于 100MB""",
    ),
    (
        ("ps aux", "grep python"),
        """EXPLAIN: 列出系统上所有正在运行的进程，再把结果交给 grep，只留下包含 python 字样的那些行。纯读取，不会结束进程。
---
COMMAND: ps aux | grep python
RISK: SAFE
REASON: 只读取进程列表并做文本筛选
PARAMS: ps aux=显示所有用户的全部进程; |=管道，把左边结果交给右边; grep python=按文本筛选""",
    ),
    (
        ("删掉整个 /var", "删除 /var", "rm -rf /var"),
        """EXPLAIN: 这个操作会删除系统目录，通常无法恢复。这里给出一个只读的替代命令，先看看是什么占了空间。
---
COMMAND: rm -rf /var
RISK: DENY
REASON: 递归强制删除 + 系统级路径 /var，不可逆
PARAMS: rm=删除; -rf=递归且强制; /var=系统级目录""",
    ),
    (
        ("都能读写", "777", "chmod -r"),
        """EXPLAIN: 这会让本机任何账号都能改写项目文件，权限开放得过大。
---
COMMAND: chmod -R 777 ./project
RISK: CAUTION
REASON: 递归改权限且开放到 777，命令可逆但暴露面变大
PARAMS: chmod=修改权限; -R=递归到子目录; 777=所有账号可读写执行; ./project=目标目录""",
    ),
    (
        ("解压", "tar.gz"),
        """EXPLAIN: 把 .tar.gz 压缩包解压到指定目录，-C 指定目标位置，不会覆盖包外的文件。
---
COMMAND: tar -xzf archive.tar.gz -C ./target
RISK: SAFE
REASON: 解压到明确目录，不删除原文件
PARAMS: tar=归档工具; -xzf=解压 gzip 压缩包; archive.tar.gz=源文件; -C ./target=解压到指定目录""",
    ),
    (
        ("报错", "error is not recoverable", "exiting now"),
        """EXPLAIN: tar 读到了损坏或不完整的数据于是主动中止，最常见是压缩包没下载完整，或解压参数与实际格式不匹配。
---
COMMAND: file archive.tar.gz
RISK: SAFE
REASON: 只读地识别文件类型，用于排查
PARAMS: file=识别文件类型; archive.tar.gz=待检查的文件""",
    ),
    (
        ("查看端口", "端口占用"),
        """EXPLAIN: 列出正在监听的 TCP 端口和对应进程，只读。
---
COMMAND: ss -tulpn
RISK: SAFE
REASON: 只读取网络连接状态
PARAMS: ss=查看套接字; -tulpn=显示 TCP/UDP 监听端口与进程号""",
    ),
]

_MOCK_DEFAULT = """EXPLAIN: 先列出当前目录下的文件（含隐藏文件）和详细信息，这是只读操作，方便你确认接下来要处理什么。
---
COMMAND: ls -la
RISK: SAFE
REASON: 只读取目录内容
PARAMS: ls=列出目录内容; -la=显示隐藏文件与详细信息"""


def mock_response(query: str, mode: str) -> str:
    lowered = query.lower()
    if mode == MODE_CMD2NL and not any(k in lowered for k in ("ps aux", "grep python")):
        return (
            "EXPLAIN: 这条命令读取信息并做筛选，不会改动文件。\n"
            "---\n"
            f"COMMAND: {query.strip()}\n"
            "RISK: SAFE\n"
            "REASON: 未发现写入或删除动作\n"
            f"PARAMS: {query.strip().split()[0] if query.strip() else '命令'}=命令主体"
        )
    for keywords, body in _MOCK_CASES:
        if any(k in lowered for k in keywords):
            return body
    return _MOCK_DEFAULT


def _chunk(text: str, size: int = 12) -> Iterator[str]:
    for i in range(0, len(text), size):
        yield text[i : i + size]


class ModelClient:
    """统一的流式接口。所有上层代码只认识 StreamEvent，不关心后端是谁。"""

    def __init__(self, cfg: Config, env: EnvInfo) -> None:
        self.cfg = cfg
        self.env = env
        # 云端模式下由 _stream_cloud 填充，供 app.run_once 在门控后叠加团队策略，
        # 避免为拿同一份策略再发一次 HTTP 请求。
        self.team_policy: team_policy.TeamPolicy | None = None

    # -- 公开入口 -----------------------------------------------------------
    def stream(
        self,
        query: str,
        mode: str = MODE_NL2CMD,
        history: str | None = None,
    ) -> Iterator[StreamEvent]:
        try:
            if self.cfg.cloud_ready():
                yield from self._stream_cloud(query, mode, history)
                return
            if self.cfg.mock:
                yield from self._stream_mock(query, mode)
                return
            try:
                yield from self._stream_ollama(query, mode, history)
            except (ModelUnavailableError, ModelTimeoutError) as ollama_err:
                if self.cfg.cloud_fallback and self.cfg.cloud_endpoint:
                    yield from self._fallback_cloud(query, mode, history, ollama_err)
                else:
                    raise
        except (ModelUnavailableError, ModelTimeoutError, ParseError, CloudDisabledError) as exc:
            yield StreamEvent(kind="error", error=exc)
        except Exception as exc:  # 兜底：任何意外都转成可读提示，不抛裸栈
            yield StreamEvent(kind="error", error=ModelUnavailableError(str(exc)))

    # -- Ollama ------------------------------------------------------------
    def _payload(self, query: str, mode: str, history: str | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "system": build_system_prompt(self.env, self.cfg.response_format),
            "prompt": build_user_prompt(query, mode, history),
            "stream": True,
            # Qwen3 双模式：这里强制关掉思考链，否则会击穿响应时间目标
            "think": False if self.cfg.non_thinking else True,
            "options": {
                "temperature": 0.1,
                "num_ctx": 2048,
                "num_predict": 320,
            },
        }
        if self.cfg.response_format == "json":
            payload["format"] = JSON_SCHEMA
        return payload

    def _stream_ollama(
        self, query: str, mode: str, history: str | None
    ) -> Iterator[StreamEvent]:
        url = f"{self.cfg.host}/api/generate"
        body = json.dumps(self._payload(query, mode, history), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        parser = _LineProtocolParser()
        raw_parts: list[str] = []
        try:
            with _open(req, timeout=self.cfg.timeout) as resp:
                for line in resp:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    delta = data.get("response", "")
                    if delta:
                        raw_parts.append(delta)
                        if self.cfg.response_format == "lines":
                            chunk = parser.feed(delta)
                            if chunk:
                                yield StreamEvent(kind="explain_delta", text=chunk)
                            elif parser.sealed:
                                yield StreamEvent(kind="buffered")
                        # json 模式：整段缓冲，不做流式解释
                    if data.get("error"):
                        raise ModelUnavailableError(str(data["error"]))
        except urllib.error.HTTPError as exc:
            detail = f"HTTP {exc.code}"
            if exc.code == 404:
                detail += f"，本机可能还没有 {self.cfg.model}"
            raise ModelUnavailableError(detail) from exc
        except urllib.error.URLError as exc:
            raise ModelUnavailableError(getattr(exc, "reason", str(exc))) from exc
        except socket.timeout as exc:
            raise ModelTimeoutError(self.cfg.timeout) from exc

        raw = "".join(raw_parts)
        if self.cfg.response_format == "lines":
            tail = parser.finalize()
            if tail:
                yield StreamEvent(kind="explain_delta", text=tail)
        result = (
            parse_json_protocol(raw)
            if self.cfg.response_format == "json"
            else parse_line_protocol(raw)
        )
        yield StreamEvent(kind="done", result=result)

    # -- 云端兜底（本地 Ollama 不可达时由 stream() 触发，force 跳过开关检查）--
    def _fallback_cloud(
        self, query: str, mode: str, history: str | None, reason: Exception
    ) -> Iterator[StreamEvent]:
        yield StreamEvent(
            kind="notice",
            text=f"本地模型不可用（{reason}），已回退到云端模式 · 数据将离开本机",
        )
        yield from self._stream_cloud(query, mode, history, force=True)

    # -- 线上体验模式（默认关闭） -------------------------------------------
    def _cloud_model_for(self, mode: str) -> str:
        """按任务类型路由：命令生成（编程）走深度档，解释/摘要走轻量档。"""
        if mode == MODE_NL2CMD:
            return self.cfg.cloud_model_deep
        return self.cfg.cloud_model

    def _cloud_candidates(self, mode: str, tp: team_policy.TeamPolicy) -> list[str]:
        """构造候选模型链：档位优先 + 策略过滤（停用模型 / 白名单）。

        保持向后兼容：若用户用 SHELLMATE_CLOUD_MODEL(_DEEP) 指定了旧的单模型，
        且它不在候选链里，则把它插到最前面，确保显式配置优先。
        """
        raw = (self.cfg.cloud_models_deep if mode == MODE_NL2CMD
               else self.cfg.cloud_models_light)
        chain = [m.strip() for m in raw.split(",") if m.strip()]
        single = self._cloud_model_for(mode)
        if single and single not in chain:
            chain.insert(0, single)
        if not chain:
            chain = [single] if single else []
        return team_policy.candidate_models(chain, tp)

    def _stream_cloud(
        self, query: str, mode: str, history: str | None, force: bool = False
    ) -> Iterator[StreamEvent]:
        if not self.cfg.cloud_enabled and not force:
            raise CloudDisabledError()

        # 团队策略：拉取失败静默降级为「无额外策略」，绝不阻断主链路。
        tp = (team_policy.fetch_policy(self.cfg.cloud_endpoint, token=self.cfg.cloud_token)
              if self.cfg.policy_fetch else team_policy.TeamPolicy())
        self.team_policy = tp
        candidates = self._cloud_candidates(mode, tp)
        if not candidates:
            raise ModelUnavailableError("没有可用的云端模型（均已被团队策略停用或不在白名单内）",
                                       **_CLOUD_ERR)

        messages = [
            {"role": "system", "content": build_system_prompt(self.env, self.cfg.response_format)},
            {"role": "user", "content": build_user_prompt(query, mode, history)},
        ]
        headers = {
            "Content-Type": "application/json",
            "X-Client-ID": self.cfg.cloud_client_id,
            "X-ShellMate-Mode": mode,
        }
        if self.cfg.cloud_api_key:
            headers["Authorization"] = f"Bearer {self.cfg.cloud_api_key}"
        if self.cfg.cloud_token:
            headers["X-API-Token"] = self.cfg.cloud_token

        def _http(model: str) -> dict[str, Any]:
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "temperature": 0.1,
                "max_tokens": tp.max_tokens,
            }
            req = urllib.request.Request(
                self.cfg.cloud_endpoint,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with _open(req, timeout=self.cfg.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                # 403 = 网关按团队政策停用了该模型；429/5xx = 过载 / 上游抖动。
                # 这些都换下一个候选模型；401/404 等配置错误也照同样方式兜底。
                detail = {403: "模型已被停用", 429: "触发限流"}.get(exc.code, "")
                if exc.code in (403, 429, 500, 502, 503, 504):
                    raise ModelUnavailableError(
                        f"{detail or '上游临时不可用'} HTTP {exc.code}", **_CLOUD_ERR) from exc
                raise ModelUnavailableError(f"线上模型调用失败：HTTP {exc.code}",
                                           **_CLOUD_ERR) from exc
            except (urllib.error.URLError, socket.timeout, json.JSONDecodeError) as exc:
                raise ModelUnavailableError(f"线上模型调用失败：{exc}", **_CLOUD_ERR) from exc

        last_error: Exception | None = None
        for index, model in enumerate(candidates):
            try:
                data = _http(model)
                content = data["choices"][0]["message"]["content"]
                # 严格协议解析失败也换模型：不同免费模型对行协议的遵循度差异很大
                result = (
                    parse_json_protocol(content)
                    if self.cfg.response_format == "json"
                    else parse_line_protocol(content)
                )
            except (KeyError, IndexError, TypeError) as exc:
                last_error = ParseError("线上模型返回结构不符合预期")
                if index + 1 < len(candidates):
                    yield StreamEvent(
                        kind="notice",
                        text=f"{model} 返回结构异常，自动改用 {candidates[index + 1]}",
                    )
                    continue
                raise last_error from exc
            except (ModelUnavailableError, ParseError) as exc:
                last_error = exc
                if index + 1 < len(candidates):
                    yield StreamEvent(
                        kind="notice",
                        text=f"{model} 暂时不可用或输出不合协议（{exc}），自动改用 {candidates[index + 1]}",
                    )
                    continue
                raise
            result.from_cloud = True
            result.cloud_model = model
            yield StreamEvent(kind="explain_delta", text=result.explain)
            yield StreamEvent(kind="done", result=result)
            return
        raise ModelUnavailableError(str(last_error) if last_error else "全部候选模型均不可用")

    # -- mock ---------------------------------------------------------------
    def _stream_mock(self, query: str, mode: str) -> Iterator[StreamEvent]:
        raw = mock_response(query, mode)
        parser = _LineProtocolParser()
        if self.cfg.response_format == "json":
            yield StreamEvent(kind="explain_delta", text=parse_line_protocol(raw).explain)
            yield StreamEvent(kind="done", result=_mock_as_json(raw))
            return
        for piece in _chunk(raw):
            chunk = parser.feed(piece)
            if chunk:
                yield StreamEvent(kind="explain_delta", text=chunk)
            elif parser.sealed:
                yield StreamEvent(kind="buffered")
        tail = parser.finalize()
        if tail:
            yield StreamEvent(kind="explain_delta", text=tail)
        yield StreamEvent(kind="done", result=parse_line_protocol(raw))


def _mock_as_json(raw: str) -> CommandResult:
    """把行协议样例转成 JSON 协议下的等价结果，供 --format json 自测。"""
    parsed = parse_line_protocol(raw)
    payload = {
        "explain": parsed.explain,
        "command": parsed.command,
        "risk_level": parsed.risk_level,
        "reason": parsed.reason,
        "params": [{"token": p.token, "meaning": p.meaning} for p in parsed.params],
    }
    return parse_json_protocol(json.dumps(payload, ensure_ascii=False))
