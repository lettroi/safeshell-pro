"""命令助手：自然语言 → 安全命令 → 执行后果模拟。

设计红线（与 ShellMate 一脉相承）：
- **只生成 · 不执行 · 不隔离**：本模块从头到尾只做「文本 → 文本」。模拟引擎是
  纯静态规则推演，绝不调用 subprocess / os.system / eval / exec，也不读写被推演的
  目标路径。它告诉你「如果执行会发生什么」，但永远不会真的执行。
- 安全判断由确定性门控（gate.check）覆盖模型自评；DENY 时命令被清空，任何返回
  路径都不会把它交给用户。
- 命令生成复用 raw.raw_completion()：凭据路由、熔断、停用、审计口径全部继承，
  不另起一套上游调用。

对外入口：
    generate_command(query, mode="nl2cmd") -> dict   # 完整编排
    simulate_command(command, verdict=None) -> dict  # 单独模拟一条命令文本
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from . import raw

try:  # 只读目录浏览是可选增强：缺失或不可用时优雅降级为抽象示例
    from . import fsview
except ImportError:  # pragma: no cover - 部署裁剪场景
    fsview = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 1) 提示词（移植 ShellMate prompt.py 的铁律：non-thinking + 行协议 + 不执行）
# ---------------------------------------------------------------------------

LINE_PROTOCOL = """EXPLAIN: <一句中文白话解释，不超过两行，不要换行>
---
COMMAND: <可直接复制的命令；一条命令写一行，需要多条就写多行>
RISK: <SAFE 或 CAUTION 或 DENY，你的初步判断>
REASON: <一句话说明为什么是这个风险等级>
PARAMS: <片段>=<含义>; <片段>=<含义>"""

_ENV_BLOCK = (
    "目标环境：Linux（常见发行版），交互式 bash，普通用户权限。"
    "命令会在一台开发机上由用户手动复制执行；用户是不懂命令行的初学者。"
)


def build_system_prompt() -> str:
    return (
        "你是一个耐心的 Linux 命令行老师，面向完全不懂命令行的初学者。\n"
        f"{_ENV_BLOCK}\n"
        "硬性要求：\n"
        "- 忠实完成用户的要求：用户要删除就给删除命令、要查看就给查看命令，"
        "绝不能偷偷把用户要的操作换成另一种操作（例如把「删除」换成「查看」）。"
        "命令是否危险由后面的安全系统判断和提示，你只负责准确表达用户的意图。\n"
        "- COMMAND 段写可直接复制的命令，不要 Markdown 代码块、不要反引号、不要前后缀说明。"
        "一条命令占一行；若任务确实需要多条命令才能完成，就逐行都写出来，不要合并、不要省略。\n"
        "- 用中文写白话解释，让没用过终端的人也能听懂；顺带说清这条命令会不会改动文件。\n"
        "- 只使用真实存在的命令和参数，绝不编造选项。\n"
        "- 路径要真实具体：用户给了路径就用用户的路径；用户没给具体路径时，"
        "用 /tmp、当前目录 . 等常见的真实位置，绝不要写 /path/to/、/xxx/、<路径> 这类占位符。\n"
        "- 不做任何执行动作；你只负责写出命令文本。\n"
        "- 不输出思考过程、不输出分析步骤、不输出「让我想想」。\n"
        "- 不要说教、不要在命令前后附加安全建议；更安全的替代做法由系统统一给出。\n"
        "\n严格按下面的格式输出，除格式字段外不要加任何其他内容：\n" + LINE_PROTOCOL
    )


def build_user_prompt(query: str, mode: str = "nl2cmd") -> str:
    if mode == "cmd2nl":
        return (
            "任务：解释下面这条已有命令。COMMAND 原样抄回这条命令，"
            "EXPLAIN 用中文说明它整体做什么、会不会改动系统，PARAMS 逐段拆解。\n"
            f"命令：{query}"
        )
    return (
        "任务：把下面的自然语言需求转成一条可直接使用的命令，忠实表达用户要做的事。"
        "如果一条命令不够、需要多条命令按顺序完成，就把每条命令各写一行。\n"
        f"需求：{query}"
    )


# ---------------------------------------------------------------------------
# 2) 行协议严格解析（移植 ShellMate client.parse_line_protocol）
# ---------------------------------------------------------------------------

class ParseError(Exception):
    """模型输出不符合行协议契约。"""


@dataclass
class ParsedCommand:
    command: str                              # 兼容字段：多条命令用 \n 连接
    commands: list[str] = field(default_factory=list)  # 逐行命令
    risk_level: str = "SAFE"
    reason: str = ""
    explain: str = ""
    params: list[dict[str, str]] = field(default_factory=list)
    model_risk: str = ""


def parse_line_protocol(text: str) -> ParsedCommand:
    """严格解析；任何必需字段缺失/非法都抛 ParseError，绝不猜测补齐。

    COMMAND 段允许多行：每条命令占一行。RISK/REASON/EXPLAIN/PARAMS 的续行仍按空格拼接。
    """
    raw_text = (text or "").strip()
    fields: dict[str, str] = {}
    current: str | None = None
    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped == "---":
            current = None
            continue
        if not stripped:
            continue
        known = False
        for key in ("EXPLAIN", "COMMAND", "RISK", "REASON", "PARAMS"):
            prefix = key + ":"
            if stripped.startswith(prefix):
                value = stripped[len(prefix):].strip()
                # COMMAND 多行：后续没有字段标记的行都是命令，用 \n 连接
                fields[key] = (fields.get(key, "") + ("\n" if key == "COMMAND" and fields.get(key) else " ") + value) if fields.get(key) else value
                current = key
                known = True
                break
        # 续行：COMMAND 用换行保留多条命令，其它字段用空格拼接
        if not known and current is not None:
            sep = "\n" if current == "COMMAND" else " "
            fields[current] = (fields.get(current, "") + sep + stripped).strip()

    # 核心四字段严格必填；PARAMS 只是辅助展示，缺失不应阻断命令生成
    for required in ("EXPLAIN", "COMMAND", "RISK", "REASON"):
        if not fields.get(required):
            raise ParseError(f"缺少 {required} 字段")

    command_field = fields["COMMAND"].strip()
    if "```" in command_field:
        raise ParseError("命令里混入了 Markdown 代码块")
    # 逐行清洗：每行一条命令，剥掉反引号包裹
    commands = [ln.strip().strip("`").strip() for ln in command_field.splitlines()]
    commands = [ln for ln in commands if ln]
    if not commands:
        raise ParseError("没有有效的命令行")
    if any("\t" in ln for ln in commands):
        # 制表符在命令里通常是排版残留，保留但不作为换行依据；这里不拦截
        pass
    command = "\n".join(commands)

    risk = fields["RISK"].strip().upper()
    if risk not in {"SAFE", "CAUTION", "DENY"}:
        raise ParseError(f"RISK 值非法：{risk}")

    params: list[dict[str, str]] = []
    params_field = fields.get("PARAMS", "").strip()
    # 模型习惯两种写法：分号分隔 `find=查找; -size=按大小`，或空白分隔 `a=xx b=yy`。
    # 统一按「空白或分号 + 形如 token= 的分界」切分，兼容两者。
    chunks = re.split(r"[;\n]\s*|(?<=\S)\s+(?=[A-Za-z0-9_\-./]{1,24}=)", params_field) if params_field else []
    for chunk in chunks:
        chunk = chunk.strip().strip(";").strip()
        if not chunk or "=" not in chunk:
            continue
        token, meaning = chunk.split("=", 1)
        token, meaning = token.strip(), meaning.strip()
        if token and meaning:
            params.append({"token": token, "meaning": meaning})

    return ParsedCommand(
        command=command,
        commands=commands,
        risk_level=risk,
        model_risk=risk,
        reason=fields["REASON"],
        explain=fields["EXPLAIN"],
        params=params,
    )


# ---------------------------------------------------------------------------
# 3) 确定性门控（移植 ShellMate gate.py 的核心；去掉对客户端 CommandResult 的依赖）
#    纯函数，无 IO、无执行。模型自评 risk 只留痕，最终结论以这里为准。
# ---------------------------------------------------------------------------

@dataclass
class Verdict:
    level: str = "SAFE"
    matched_rules: list[str] = field(default_factory=list)
    unverified: bool = False
    reason: str = ""
    consequences: str = ""
    alternatives: list[str] = field(default_factory=list)


LEVEL_ORDER = {"SAFE": 0, "CAUTION": 1, "DENY": 2}
KNOWN_COMMANDS = frozenset(
    """ls pwd cd find grep egrep fgrep rg cat head tail wc sort uniq cut awk sed
    ps top htop df du free uptime who whoami id date echo printf file stat which
    whereis type man help history env printenv uname hostname ss netstat ip ping
    curl wget tar gzip gunzip zip unzip unxz xz bzip2 diff cmp tee tree less more
    basename dirname realpath readlink sha256sum md5sum jq column tr paste comm
    join nl rev tac seq watch lsof mount blkid lsblk fdisk dmesg journalctl git
    docker kubectl systemctl service kill pkill pgrep nice nohup sleep time test
    mkdir touch cp mv rm rmdir ln chmod chown chgrp dd mkfs mkfs.ext4 shred truncate sync
    crontab alias export unset source bash sh zsh fish sudo su python python3 pip
    node npm make cmake gcc apt apt-get yum dnf pacman snap flatpak
    xargs getconf nproc lscpu lsmod modprobe loginctl timedatectl
    hostnamectl localectl resolvectl openssl ssh scp rsync true false""".split()
)
SYSTEM_PATHS = frozenset(
    """/ /var /etc /usr /bin /sbin /boot /dev /sys /proc /lib /lib64 /opt /root
    /home /srv /run /mnt /media""".split()
)
PERMISSIVE_MODES = {"777", "666", "000", "a+rwx", "o+rwx", "0777", "a=rwx"}
_SPLIT_RE = re.compile(r"(?:\|\||&&|;|\||\n)")
_SUBSHELL_RE = re.compile(r"\$\(|`|>\s*/dev/(?:sd|nvme|hd|mmcblk)|:\(\)\s*\{")
_BLOCK_DEVICE_RE = re.compile(r"^/dev/(?:sd|nvme|hd|mmcblk|loop|vd)")
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# 模型编造的占位符路径：这类命令复制去执行会报「文件不存在」，必须提醒用户替换
_PLACEHOLDER_RE = re.compile(
    r"/path/to/|/your[_-]|/example/|<路径>|<path>|/xxx/|/placeholder/|your[_-]?(file|dir|path)|\bTODO\b",
    re.I,
)

_CONS_DELETE = "删除不可恢复，终端里没有回收站；路径写错或变量为空时，影响范围可能远超预期。"
_CONS_777 = "这会让本机任何账号都能改写这些文件；若目录位于共享目录或 Web 根目录下，风险更高。"
_ALT_DELETE = ("du -sh <目录>/* | sort -h 先看体积", "mv 到临时目录观察几天再处理", "删除前先做一份备份")


def _is_system_path(token: str) -> bool:
    t = token.strip().strip("'\"").rstrip("*").rstrip("/") or "/"
    if t in SYSTEM_PATHS or t in {"~", "$HOME", "${HOME}"}:
        return True
    return any(t.startswith(p + "/") for p in SYSTEM_PATHS if p != "/")


def _paths(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for i, tok in enumerate(tokens[1:], start=1):
        if tok.startswith("-") and i > 0 and tokens[i - 1] not in {"-size", "-name"}:
            continue
        if i > 0 and tokens[i - 1] in {"-C", "-f", "-o", "-t", "-d", "-path", "-type"}:
            continue
        if "/" in tok or tok in {".", "..", "~"} or tok.startswith("$") or tok.startswith("*"):
            out.append(tok)
    return out


def _flags(tokens: list[str]) -> set[str]:
    flags: set[str] = set()
    for tok in tokens[1:]:
        if tok.startswith("--"):
            flags.add(tok.split("=", 1)[0])
        elif tok.startswith("-") and len(tok) > 1 and not tok[1].isdigit():
            flags.update("-" + ch for ch in tok[1:])
    return flags


def _strip_prefixes(tokens: list[str]) -> tuple[list[str], bool]:
    rest = list(tokens)
    while rest and _ENV_ASSIGN_RE.match(rest[0]):
        rest.pop(0)
    if rest and rest[0] == "env":
        rest.pop(0)
    privileged = bool(rest and rest[0] in {"sudo", "su"})
    if privileged:
        rest = rest[1:]
    return rest, privileged


def _record(v: Verdict, rule_id: str, level: str, consequences: str = "",
            alternatives: tuple[str, ...] = ()) -> None:
    if rule_id not in v.matched_rules:
        v.matched_rules.append(rule_id)
    if LEVEL_ORDER[level] > LEVEL_ORDER[v.level]:
        v.level = level
    if consequences and not v.consequences:
        v.consequences = consequences
    for alt in alternatives:
        if alt not in v.alternatives:
            v.alternatives.append(alt)


def _check_segments(command: str, v: Verdict) -> None:
    for segment in (p.strip() for p in _SPLIT_RE.split(command)):
        if not segment:
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            _record(v, "CAUTION-UNPARSEABLE", "CAUTION", "引号没有闭合，命令结构无法可靠解析。")
            v.unverified = True
            continue
        tokens, privileged = _strip_prefixes(tokens)
        if privileged:
            _record(v, "CAUTION-SUDO", "CAUTION", "提权后误操作的影响范围会放大到整个系统。",
                    ("先不加 sudo 试一次，确认确实需要再提权",))
        if not tokens:
            continue
        head = tokens[0]
        if head not in KNOWN_COMMANDS:
            v.unverified = True

        flags = _flags(tokens)
        paths = _paths(tokens)
        # DENY 规则
        if head in {"rm", "rmdir", "shred"} and (
            flags & {"-r", "-R", "--recursive", "-f", "--force"} or head == "shred"
        ) and any(_is_system_path(p) or p.startswith("$") for p in paths):
            _record(v, "DENY-DELETE-SYSTEM", "DENY",
                    "/var 等系统目录存放日志、缓存与包管理数据库，整目录删除后通常无法恢复，机器可能无法启动。",
                    tuple(_ALT_DELETE))
        if head in {"dd", "mkfs", "mkfs.ext4", "mkfs.xfs", "shred"}:
            danger = any(tok.startswith("of=") and _BLOCK_DEVICE_RE.match(tok[3:]) for tok in tokens) if head == "dd" \
                else any(_BLOCK_DEVICE_RE.match(p) for p in paths)
            if danger:
                _record(v, "DENY-BLOCK-DEVICE", "DENY", "会覆盖磁盘或分区上的原始数据，几乎不可能恢复。",
                        ("先确认设备名：lsblk", "操作前做完整镜像备份"))
        if re.search(r"(?:curl|wget)[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b", segment, re.I):
            _record(v, "DENY-REMOTE-EXEC", "DENY",
                    "会在本机执行一段你还没看过的脚本，内容随时可变，无法预先判断后果。",
                    ("curl -o script.sh <URL> 下载后先读一遍再决定",))
        if re.search(r":\(\)\s*\{.*\|\s*:\s*&", segment):
            _record(v, "DENY-FORK-BOMB", "DENY", "会瞬间耗尽进程配额导致机器卡死，通常需要强制重启。")
        if re.search(r">\s*(?:/etc|/usr|/boot|/var)/", segment):
            _record(v, "DENY-OVERWRITE-SYSTEM", "DENY", "会直接覆盖系统配置或服务文件，可能导致无法启动。",
                    ("先备份原文件：cp -a <文件> <文件>.bak",))
        # CAUTION 规则
        if head in {"chmod", "chown"} and (flags & {"-r", "-R", "--recursive"}) and \
                any(t.strip("'\"") in PERMISSIVE_MODES for t in tokens[1:]):
            _record(v, "CAUTION-777", "CAUTION", _CONS_777, ("chmod -R 755 ./project",))
        if head in {"rm", "rmdir", "unlink", "shred"}:
            _record(v, "CAUTION-DELETE", "CAUTION", _CONS_DELETE,
                    ("mv 到临时目录观察几天再处理", "删除前先做备份"))
        if head == "git" and ({"--hard", "-f", "--force"} & flags or
                              (len(tokens) > 2 and tokens[1] in {"reset", "clean", "push"})):
            _record(v, "CAUTION-GIT-DESTRUCTIVE", "CAUTION", "可能丢弃未提交的工作区改动，且不易恢复。",
                    ("先运行 git status 看清改动", "用 git stash 暂存而不是 --hard"))
        if head == "find" and ("-delete" in tokens or "-exec" in tokens):
            _record(v, "CAUTION-FIND-DELETE", "CAUTION", "匹配范围稍写错就会批量删除，且不可恢复。",
                    ("先去掉 -delete，只加 -print 看一遍匹配结果",))
        if head in {"systemctl", "service", "kill", "pkill"} and \
                any(t in {"stop", "disable", "restart", "-9", "--force"} for t in tokens[1:]):
            _record(v, "CAUTION-SERVICE", "CAUTION", "可能中断正在运行的服务或丢失未保存数据。",
                    ("先用 ps / systemctl status 确认目标",))
        if re.search(r"(?<![\d=\-])>\s*[^\s]", segment) and not segment.strip().startswith("#"):
            _record(v, "CAUTION-REDIRECT", "CAUTION", "会覆盖目标文件的原有内容。",
                    ("把 > 换成 >> 追加，或先备份目标文件",))


def gate_check(command: str) -> Verdict:
    """确定性门控：纯函数，无 IO、无执行。"""
    v = Verdict()
    if not command.strip():
        _record(v, "CAUTION-EMPTY", "CAUTION", "没有拿到可检查的命令文本。")
        v.reason = "空命令"
        return v
    if _SUBSHELL_RE.search(command):
        _record(v, "CAUTION-SUBSHELL", "CAUTION", "含命令替换或重定向到设备，执行范围不易一眼看清。")
    if _PLACEHOLDER_RE.search(command):
        _record(v, "CAUTION-PLACEHOLDER", "CAUTION",
                "命令里含有占位符路径（如 /path/to/），这是模板不是真实位置，直接复制执行会报「找不到文件」；"
                "请把它换成你自己机器上的真实路径。")
    # 跨管道/分号的模式先用整条原文判定
    if re.search(r"(?:curl|wget)[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b", command, re.I):
        _record(v, "DENY-REMOTE-EXEC", "DENY", "会在本机执行一段你还没看过的脚本，无法预先判断后果。")
    if re.search(r":\(\)\s*\{.*\|\s*:\s*&", command):
        _record(v, "DENY-FORK-BOMB", "DENY", "会瞬间耗尽进程配额导致机器卡死。")
    if re.search(r">\s*(?:/etc|/usr|/boot|/var)/", command):
        _record(v, "DENY-OVERWRITE-SYSTEM", "DENY", "会直接覆盖系统配置或服务文件。")
    _check_segments(command, v)
    if v.level == "SAFE":
        v.reason = "未命中写入/删除/提权类规则，按只读命令处理"
    elif v.level == "CAUTION":
        v.reason = "命令可用，但改动范围或权限开放程度需要你先确认"
    else:
        v.reason = "命中不可逆规则，已拒绝输出命令"
    return v


# ---------------------------------------------------------------------------
# 4) 执行后果模拟引擎（纯静态推演，零执行原语）
#    仅解析命令文本，按命令族推导「如果执行会发生什么」；不碰真实文件系统。
# ---------------------------------------------------------------------------

@dataclass
class Effect:
    kind: str      # read | create | modify | delete | network | permission | process | package | archive | unknown
    target: str
    detail: str
    command: str = ""  # 产生这条后果的具体命令片段（如 ls -lt / grep error / wc -l）


# 命令族 → 只读判定（这些族默认不改变系统状态）
_READ_ONLY = set("""ls pwd cat head tail grep egrep fgrep rg wc sort uniq cut awk sed
ps top htop free uptime who whoami id date stat which whereis type env printenv
uname hostname ss netstat ip ping lsof mount lsblk blkid dmesg journalctl
tree less more file sha256sum md5sum diff cmp jq nl rev tac seq nproc lscpu
getconf printenv hostnamectl timedatectl du df find column tr paste comm join
basename dirname realpath readlink echo printf history man help sleep time test
true false""".split())
_CREATE = {"mkdir", "touch"}
_COPY = {"cp", "mv", "ln"}
_DELETE = {"rm", "rmdir", "unlink", "shred", "truncate"}
_PERMISSION = {"chmod", "chown", "chgrp"}
_NETWORK = {"curl", "wget", "ssh", "scp", "rsync"}
_PACKAGE = {"apt", "apt-get", "yum", "dnf", "pacman", "pip", "pip3", "npm", "snap", "flatpak"}
_PROCESS = {"systemctl", "service", "kill", "pkill", "pgrep", "killall"}
_ARCHIVE = {"tar", "zip", "unzip", "gzip", "gunzip", "xz", "unxz", "bzip2"}
_DISK = {"dd", "mkfs", "mkfs.ext4", "mkfs.xfs", "fdisk"}

# ---------------------------------------------------------------------------
# 选项语义层：把命令参数翻译成小白能懂的白话（用于模拟文案，纯查表，零执行）
# ---------------------------------------------------------------------------

# find -printf 的格式符 → 人话
_PRINTF_DIRECTIVES: tuple[tuple[str, str], ...] = (
    ("%T@", "最后修改时间（时间戳）"), ("%TY", "修改年份"), ("%Tm", "修改月份"),
    ("%Td", "修改日期"), ("%TH", "修改小时"), ("%TM", "修改分钟"),
    ("%TS", "修改秒数"), ("%Tr", "修改时间"), ("%T+", "完整修改时间"),
    ("%p", "文件完整路径"), ("%f", "文件名"), ("%s", "文件大小"),
    ("%n", "硬链接数"), ("%u", "所有者"), ("%g", "所属组"), ("%m", "权限"),
)

# 通用选项 → 人话（按出现顺序展示）
_OPTION_MEANINGS: dict[str, str] = {
    "-type": "按类型筛选", "-f": "普通文件", "-d": "仅目录",
    "-name": "按名称匹配", "-iname": "忽略大小写匹配",
    "-size": "按大小筛选", "-mtime": "按修改时间筛选",
    "-maxdepth": "限制查找深度", "-print": "列出路径",
    "-r": "倒序", "-n": "按数字排序/指定行数", "-h": "易读单位",
    "-a": "含隐藏文件", "-l": "详细信息", "-v": "显示过程",
    "-i": "忽略大小写", "-c": "只显示行数", "-w": "只显示词数",
    "--max-count": "限制条数",
}

# 管道中常见「消费上游数据」的命令：它们处理的是上一步的输出，不是磁盘上的文件
_PIPE_CONSUMERS = {"sort", "head", "tail", "grep", "egrep", "fgrep", "rg", "wc",
                   "uniq", "cut", "awk", "sed", "tr", "column", "jq", "less", "more"}


def _describe_printf(fmt: str) -> str:
    """把 find -printf 的格式串翻译成人话，例如 '%T@ %p\\n' → 最后修改时间、文件完整路径（逐行）。"""
    hits: list[str] = []
    for token, meaning in _PRINTF_DIRECTIVES:
        if token in fmt and meaning not in hits:
            hits.append(meaning)
    if "\\n" in fmt:
        hits.append("每条占一行")
    return "、".join(hits) if hits else "按指定格式输出"


def _friendly_path(target: str) -> str:
    """把 shell 路径符号翻译成小白能懂的说法。纯文本替换，不碰文件系统。"""
    mapping = {
        ".": "当前文件夹", "./": "当前文件夹", "": "当前文件夹",
        "..": "上一级文件夹", "../": "上一级文件夹",
        "~": "你的家目录", "~/": "你的家目录", "$HOME": "你的家目录",
        "${HOME}": "你的家目录", "/tmp": "临时文件夹 /tmp",
    }
    if target in mapping:
        return mapping[target]
    if target.startswith("./"):
        return "当前文件夹下的 " + target[2:]
    return target


def _arg_flags(tokens: list[str]) -> list[str]:
    """挑出命令里的选项与紧跟的关键取值，翻译成人话列表。排除子命令头本身。"""
    out: list[str] = []
    seen: set[str] = set()
    head = tokens[0] if tokens else ""
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        # find -printf 的格式串单独解释
        if tok == "-printf" and i + 1 < len(tokens):
            desc = _describe_printf(tokens[i + 1])
            if desc not in seen:
                out.append(f"按格式打印（{desc}）"); seen.add(desc)
            i += 2
            continue
        # find -type f/d：把取值也翻出来（普通文件 / 目录）
        if tok == "-type" and i + 1 < len(tokens):
            value = tokens[i + 1]
            meaning = {"f": "只看普通文件", "d": "只看目录", "l": "只看符号链接"}.get(value, f"按类型 {value} 筛选")
            if meaning not in seen:
                out.append(meaning); seen.add(meaning)
            i += 2
            continue
        if tok in _OPTION_MEANINGS:
            meaning = _OPTION_MEANINGS[tok]
            # head/tail 的 -n 是「行数」，不是排序；其它命令的 -n 才可能是数值排序
            if tok == "-n" and head in {"head", "tail"}:
                meaning = "取行数"
            # 带数值的选项，把值带上更清楚
            if tok in {"-n", "--max-count", "-maxdepth", "-mtime", "-size", "-d"} and i + 1 < len(tokens):
                nxt = tokens[i + 1]
                if re.fullmatch(r"\d+", nxt):
                    meaning = f"{meaning} {nxt}"
            if meaning not in seen:
                out.append(meaning); seen.add(meaning)
        i += 1
    return out


def _summary_for(head: str, tokens: list[str], flags: set[str], paths: list[str]) -> tuple[str, list[Effect], str, str]:
    """返回 (一句话摘要, 影响列表, 可逆性, 预期输出示例)。全部为文本推演。"""
    if head in _READ_ONLY:
        target = _friendly_path(paths[0]) if paths else "当前文件夹"
        if head in {"ls", "tree"}:
            return (f"列出 {target} 的文件清单，只读取信息，不会改动任何文件。",
                    [Effect("read", target, "读取目录条目并显示名称、大小、时间等元信息")],
                    "无副作用", "（示例）total 24\ndrwxr-xr-x 2 user user 4096 ...  project/\n-rw-r--r-- 1 user user  812 ...  notes.txt")
        if head in {"cat", "head", "tail", "less", "more"}:
            return (f"读取并显示 {target} 的内容，文件本身不会改变。",
                    [Effect("read", target, "读取文件内容并输出到屏幕")],
                    "无副作用", "（示例）文件内容会原样显示在终端，可用 head/tail 限制行数")
        if head in {"grep", "egrep", "fgrep", "rg"}:
            return (f"在 {target} 中搜索匹配的内容，只读取不改动。",
                    [Effect("read", target, "逐行扫描并输出匹配的行")],
                    "无副作用", "（示例）只显示包含关键词的行，可加 -n 显示行号")
        if head == "find":
            arg_desc = _arg_flags(tokens)
            arg_clause = ("；具体条件：" + "，".join(arg_desc)) if arg_desc else ""
            if "-delete" in tokens:
                return (f"在 {target} 中按条件查找并**直接删除**匹配项，删除不可恢复{arg_clause}。",
                        [Effect("delete", target, "删除所有匹配到的文件或目录（不可恢复）" + arg_clause)],
                        "不可逆", "（示例）通常无输出；建议先去掉 -delete、加 -print 预览匹配结果")
            if "-exec" in tokens:
                return (f"在 {target} 中查找匹配项，并对每一项执行指定命令；影响取决于被执行的命令{arg_clause}。",
                        [Effect("modify", target, "对匹配项逐一执行 -exec 指定的操作" + arg_clause)],
                        "视具体命令而定", "（示例）建议先用 -print 预览匹配范围")
            if "-printf" in tokens:
                sample = "（示例）按你指定的格式逐行输出，每行一项，例如：2026-11-17 18:30 ./app.log"
            else:
                sample = "（示例）逐行列出匹配到的文件路径"
            return (f"在 {target} 中递归查找符合条件的文件，只读取不改动{arg_clause}。",
                    [Effect("read", target, "遍历目录、匹配条件并输出结果" + arg_clause)],
                    "无副作用", sample)
        if head in {"ps", "top", "htop"}:
            return "查看当前运行的进程列表，只读取信息。", [Effect("read", "进程表", "读取进程状态并显示")], "无副作用", "（示例）PID  USER  %CPU  COMMAND ..."
        if head in {"df", "du"}:
            return (f"查看 {target} 的磁盘/目录占用，只读取信息。",
                    [Effect("read", target, "统计并输出空间占用")], "无副作用", "（示例）12G  /home/user")
        return (f"执行只读命令 {head}，不会改动系统状态。",
                [Effect("read", target, f"{head} 读取信息并输出")], "无副作用", "（示例）命令结果会显示在终端")

    if head in _CREATE:
        targets = paths or ["指定路径"]
        names = "、".join(targets)
        return (f"创建 {names}；如果同名项目已存在，mkdir 会报错、touch 会更新其时间戳。",
                [Effect("create", t, "新建空目录" if head == "mkdir" else "新建空文件/更新时间戳") for t in targets],
                "可逆（可用 rm/rmdir 删除，但删除本身不可逆）",
                "（示例）成功时没有任何输出；用 ls 可以看到新项目")

    if head in _COPY:
        targets = paths or ["目标路径"]
        return (f"把源内容复制/移动到 {targets[-1]}；若目标已存在同名文件，内容会被覆盖。",
                [Effect("modify", t, "写入或移动数据到该路径") for t in targets],
                "通常可逆（保留源文件时），覆盖目标则难恢复",
                "（示例）成功时通常没有输出；用 ls -l 核对目标文件")

    if head in _DELETE:
        targets = paths or ["指定路径"]
        names = "、".join(targets)
        return (f"删除 {names}；删除后终端里没有回收站，无法直接撤销。",
                [Effect("delete", t, "移除该路径及其数据（不可恢复）") for t in targets],
                "不可逆", "（示例）成功时通常没有输出；用 ls 确认已消失")

    if head in _PERMISSION:
        targets = paths or ["指定路径"]
        return (f"修改 {targets[0]} 的权限/所有者；设置过宽（如 777）会让任何账号都能改写。",
                [Effect("permission", t, "更改权限位或属主") for t in targets],
                "可逆（改回原权限即可，前提是记得原值）",
                "（示例）成功时没有输出；用 ls -l 查看新的权限")

    if head in _NETWORK:
        host = next((t for t in tokens[1:] if t.startswith("http")), "指定地址")
        return (f"向 {host} 发起网络请求；可能下载内容或把本机数据发送出去，取决于具体参数。",
                [Effect("network", host, "建立外部网络连接并传输数据")],
                "视操作而定", "（示例）显示响应内容或下载进度；网络不通会报超时")
        # 下载后管道给 shell 的情况由门控 DENY，这里不重复展开

    if head in _PACKAGE:
        verb = next((t for t in tokens[1:] if not t.startswith("-")), "操作")
        return (f"通过 {head} 执行「{verb}」包管理操作：安装/更新/卸载软件会改变系统环境与磁盘占用。",
                [Effect("package", head, f"执行 {verb}，改动已安装软件包")],
                "通常可逆（可重新安装），但配置变更不自动回滚",
                "（示例）显示下载与安装进度；需要网络与相应权限")

    if head in _PROCESS:
        verb = next((t for t in tokens[1:] if t in {"start", "stop", "restart", "disable", "enable"}), "操作")
        target = _friendly_path(paths[0]) if paths else (tokens[2] if len(tokens) > 2 else "指定服务/进程")
        return (f"{head} 将对 {target} 执行「{verb}」：服务状态或进程会变化，正在运行的任务可能中断。",
                [Effect("process", target, f"服务/进程状态变为 {verb}")],
                "重启后可能恢复，未保存的数据不保留",
                "（示例）systemctl 显示状态变化；kill 成功时无输出")

    if head in _ARCHIVE:
        target = _friendly_path(paths[0]) if paths else "指定归档"
        return (f"用 {head} 打包/解压 {target}：可能生成新归档文件或解开覆盖已有文件。",
                [Effect("archive", target, "读写归档文件")],
                "视操作而定（解包可能覆盖同名文件）",
                "（示例）加 -v 会列出处理的文件名")

    if head in _DISK:
        return (f"⚠️ {head} 是磁盘级危险操作：可能直接改写分区表或覆盖整块设备，数据几乎无法恢复。",
                [Effect("modify", "磁盘/分区", "低级写入，可能破坏现有数据")],
                "不可逆（灾难性）", "（示例）此操作通常无确认提示，务必先核对设备名")

    if head == "git":
        verb = tokens[1] if len(tokens) > 1 else "操作"
        return (f"git {verb} 会改变仓库状态或历史；强制类参数可能丢弃未提交的改动。",
                [Effect("modify", "Git 仓库", f"执行 {verb}，可能影响工作区或提交历史")],
                "视操作而定（--hard/clean 丢弃的改动难恢复）",
                "（示例）显示提交、差异或状态信息")

    # 未知命令：诚实标注，不臆造后果
    return (f"命令 {head} 不在模拟规则库覆盖范围内，无法可靠推演后果；请先在测试环境确认。",
            [Effect("unknown", head, "未识别的命令，模拟引擎不做推测")],
            "未知", "（示例）无可用示例")


def _fs_snapshot(fs_path: str | None) -> dict[str, Any] | None:
    """取只读目录快照（仅元数据）；任何不可用都返回 None，绝不影响命令生成。"""
    if not fs_path or fsview is None:
        return None
    try:
        snap = fsview.snapshot(fs_path)
    except Exception:  # noqa: BLE001 - 目录增强失败必须静默降级
        return None
    return snap if snap.get("ok") else None


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def _glob_match(pattern: str, name: str) -> bool:
    """极简 glob：支持 * ? 与字面量，用于只读预测匹配，不调用 shell。"""
    import fnmatch
    return fnmatch.fnmatch(name, pattern)


def _concrete_preview(command: str, snap: dict[str, Any]) -> dict[str, Any]:
    """基于真实目录元数据，预测命令会命中的具体文件与输出。纯计算，零执行。

    返回 ``{"matched":[{name,size,mtime}], "lines":[...], "hint":..., "exists":...}``；
    只处理有把握的常见只读场景（ls / find / grep / head / tail / du / wc），
    其余保持抽象示例，不臆造。
    """
    files: list[dict[str, Any]] = snap.get("files", [])
    dirs: list[dict[str, Any]] = snap.get("dirs", [])
    matched: list[dict[str, Any]] = []
    lines: list[str] = []
    hint = ""
    exists: bool | None = None

    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    head = tokens[0] if tokens else ""
    rest = tokens[1:]

    # 命令里是否限定了「只看文件」与名字通配 / 大小阈值
    name_pat = None
    size_min = None
    if "-name" in rest:
        i = rest.index("-name")
        if i + 1 < len(rest):
            name_pat = rest[i + 1].strip("'\"")
    if "-size" in rest:
        i = rest.index("-size")
        if i + 1 < len(rest):
            m = re.match(r"\+(\d+)([kMGT])", rest[i + 1], re.I)
            if m:
                mult = {"k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}[m.group(2).lower()]
                size_min = int(m.group(1)) * mult

    candidates = list(files)
    if head == "ls":
        candidates = list(files) + list(dirs)
    elif head in {"du", "df"}:
        candidates = list(dirs) + list(files)
    if name_pat:
        candidates = [f for f in candidates if _glob_match(name_pat, f["name"])]
    if size_min is not None:
        candidates = [f for f in candidates if f.get("size", 0) >= size_min]

    # find -printf %T@ / %TY 等按时间排序；ls -lt / -t 同样按时间；否则按名字
    # 注意 -lt 是组合 flag，必须用 _flags() 展开，不能用 "-t" in rest
    flags = _flags(tokens)
    want_time = (
        (head == "find" and ("-printf" in rest or any(r.startswith("%T") for r in rest)))
        or "-t" in flags or "--sort=time" in rest
    )
    if want_time:
        candidates.sort(key=lambda f: f.get("mtime_ts", 0), reverse=True)
    else:
        candidates.sort(key=lambda f: f["name"].lower())

    # head/tail -n N、find -printf 常配 | head：限制条数
    n = None
    if head in {"head", "tail"} and "-n" in rest:
        i = rest.index("-n")
        if i + 1 < len(rest) and rest[i + 1].isdigit():
            n = int(rest[i + 1])
    if "|" in command or "head" in rest or "tail" in rest:
        m = re.search(r"\bhead\s+-n\s+(\d+)", command)
        if m:
            n = int(m.group(1))
    # ls | head -n 10 之类：n 已拿到；find 默认全部展示（受 MAX_ENTRIES 限制）
    shown = candidates[:n] if n else candidates[:20]

    for f in shown:
        matched.append({"name": f["name"], "size": _fmt_size(f.get("size", 0)), "mtime": f.get("mtime", "")})
        if head == "find" and "-printf" in rest:
            lines.append(f"{f.get('mtime', '')} ./{f['name']}")
        elif head == "ls" and want_time:
            lines.append(f"{f.get('mtime', '')}  {f['name']}")  # 最近修改场景展示时间
        elif head in {"ls", "du"}:
            lines.append(f"{_fmt_size(f.get('size', 0)):>8}  {f['name']}")
        else:
            lines.append(f"./{f['name']}")

    if head in {"rm", "rmdir", "unlink", "cat", "head", "tail", "grep", "cp", "mv", "chmod", "touch", "mkdir"}:
        # 取第一个像路径的参数（排除 - 开头），检查是否真实存在
        target = None
        for t in rest:
            if t.startswith("-"):
                continue
            if "/" in t or t in {".", ".."} or not t.startswith("%"):
                target = t.strip("'\"")
                break
        if target:
            base_name = target.rstrip("/").split("/")[-1] or "."
            found = any(f["name"] == base_name for f in files + dirs) if base_name not in {".", ".."} else True
            exists = found
            if not found:
                hint = (f"提示：当前目录里**没有**名为 {base_name} 的项目；"
                        f"如果就这样执行，命令多半会报 “No such file or directory”。")

    return {
        "matched": matched,
        "lines": lines,
        "hint": hint,
        "exists": exists,
        "snapshot_display": snap.get("display", ""),
        "scanned_count": snap.get("count", 0),
    }


def simulate_command(command: str, verdict: Verdict | None = None,
                     fs_path: str | None = None) -> dict[str, Any]:
    """对一条命令文本做**静态**后果模拟。纯文本推演，绝不执行、不读目标路径。

    DENY 命令：命令本身会被门控清空，这里只返回「已拦截」说明，不推演细节，
    避免给用户一种「这条危险命令还能安全试行」的错觉。
    """
    verdict = verdict or gate_check(command)
    snap = _fs_snapshot(fs_path)
    concrete = _concrete_preview(command, snap) if (snap and verdict.level != "DENY") else None
    effects: list[Effect] = []
    summaries: list[str] = []
    reversibilities: list[str] = []
    samples: list[str] = []
    heads: list[str] = []

    # 管道链：区分「第一段（读磁盘）」与「后续段（消费上一步的输出）」
    has_pipe = bool(re.search(r"\|\s*\S", command)) and "||" not in command
    seen_sources = 0  # 已经产生的输出源数量，>=1 时说明当前段在消费上游

    for segment in (p.strip() for p in _SPLIT_RE.split(command)):
        if not segment:
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            tokens = segment.split()
        tokens, _ = _strip_prefixes(tokens)
        if not tokens:
            continue
        head, flags, paths = tokens[0], _flags(tokens), _paths(tokens)
        heads.append(head)
        summary, fx, rev, sample = _summary_for(head, tokens, flags, paths)

        # 管道下游命令：纠正「当前位置」这种误导性描述，改成接收上游数据
        if has_pipe and seen_sources >= 1 and head in _PIPE_CONSUMERS:
            arg_desc = _arg_flags(tokens)
            arg_clause = ("（" + "，".join(arg_desc) + "）") if arg_desc else ""
            # head -n 10 → 直接说「前 10 条」，比「取行数 10」更自然
            n_count = None
            if head in {"head", "tail"} and "-n" in tokens:
                j = tokens.index("-n")
                if j + 1 < len(tokens) and re.fullmatch(r"\d+", tokens[j + 1]):
                    n_count = tokens[j + 1]
            verb = {
                "sort": "把上一步的结果排序", "head": f"只取上一步结果的前 {n_count} 条" if n_count else "只取上一步结果的前若干条",
                "tail": f"只取上一步结果的后 {n_count} 条" if n_count else "只取上一步结果的后若干条",
                "grep": "从上游结果里筛出匹配的行",
                "wc": "统计上一步结果的行/词数", "uniq": "去掉上游结果里相邻重复的行",
                "cut": "按列截取上游结果", "awk": "逐行处理上游结果", "sed": "按规则改写上游结果",
            }.get(head, f"继续处理上一步的结果（{head}）")
            # head/tail 已把条数写进动词，就不再重复「取行数 N」
            if head in {"head", "tail"} and n_count:
                arg_clause = ""
            summary = f"{verb}{arg_clause}，只读数据不改动文件。"
            fx = [Effect("read", "上一步的输出", f"{verb}{arg_clause}，只读内存中的数据")]

        summaries.append(summary)
        # 每条后果都绑定产生它的具体命令片段，避免界面上出现一堆不知来源的「读取…」
        for e in fx:
            if not e.command:
                e.command = segment
        effects.extend(fx)
        reversibilities.append(rev)
        samples.append(sample)
        seen_sources += 1

    writes = [e.kind in {"create", "modify", "delete", "permission", "package", "process", "archive"} for e in effects]
    network = any(e.kind == "network" for e in effects)
    if verdict.level == "DENY":
        return {
            "command": command,
            "status": "blocked",
            "head": heads[0] if heads else "",
            "summary": "这条命令已被安全门拦截，不会生成、也不会模拟执行。",
            "effects": [{"kind": "blocked", "target": "", "detail": "命中不可逆规则：" + (verdict.consequences or verdict.reason), "command": command}],
            "writes_data": False,
            "network_access": False,
            "reversible": "不适用（已拦截）",
            "sample_output": "无：命令未生成、未执行。",
            "warnings": [verdict.consequences] if verdict.consequences else [],
            "real_fs": None,
        }

    rev = "不可逆" if "不可逆" in reversibilities or "灾难性" in "".join(reversibilities) else \
          ("无副作用" if reversibilities and all(r == "无副作用" for r in reversibilities) else "部分可逆")
    warnings: list[str] = []
    if any(writes):
        warnings.append("这条命令会改变系统状态；模拟只是文字推演，实际结果取决于文件是否存在、权限和环境。")
    if network:
        warnings.append("这条命令会访问外部网络，可能把数据发出本机。")
    if verdict.level == "CAUTION" and verdict.consequences:
        warnings.append(verdict.consequences)
    warnings.extend(verdict.alternatives[:2])
    if concrete and concrete.get("hint"):
        warnings.append(concrete["hint"])

    # 管道命令：把三段拼成一条连贯叙述，避免重复啰嗦；示例只留第一条（最贴近用户所见）
    if has_pipe and len(summaries) > 1:
        parts = []
        for s in summaries:
            piece = s.rstrip("。")
            piece = re.sub(r"[，,]?(不改动任何文件|不会改动任何文件|只读数据不改动文件|文件本身不会改变)", "", piece)
            piece = piece.strip("，, ")
            if piece:
                parts.append(piece)
        joined_summary = "，接着".join(parts) + "。全程只读，不会改动任何文件。"
        shown_samples = samples[:1]
    else:
        joined_summary = " ".join(summaries) if summaries else "空命令，无可模拟内容。"
        shown_samples = samples[:2]

    # 有真实目录快照时：用真实文件名/大小/时间替换「（示例）」占位输出
    real_fs = None
    output = "\n".join(shown_samples) if shown_samples else "无"
    if concrete is not None:
        real_fs = {
            "available": True,
            "display": concrete.get("snapshot_display", ""),
            "scanned": concrete.get("scanned_count", 0),
            "matched": concrete.get("matched", []),
            "exists": concrete.get("exists"),
            "note": "基于真实目录的只读元数据预测：文件名、大小、修改时间均为真实值，但未读取文件内容。",
        }
        if concrete["lines"]:
            real_lines = concrete["lines"]
            output = ("（基于真实文件，非示例）\n" + "\n".join(real_lines))
        if concrete["hint"]:
            # hint 已进 warnings，避免重复；这里只保留真实输出
            pass

    return {
        "command": command,
        "status": "simulated",
        "head": heads[0] if heads else "",
        "summary": joined_summary,
        "effects": [{"kind": e.kind, "target": e.target, "detail": e.detail, "command": e.command} for e in effects],
        "writes_data": any(writes),
        "network_access": network,
        "reversible": rev,
        "sample_output": output,
        "warnings": list(dict.fromkeys(warnings)),
        "real_fs": real_fs,
    }


# ---------------------------------------------------------------------------
# 5) 完整编排：NL → 真实模型 → 解析 → 门控 → 模拟
# ---------------------------------------------------------------------------

async def generate_command(query: str, mode: str = "nl2cmd", model: str | None = None,
                           fs_path: str | None = None) -> dict[str, Any]:
    """端到端生成。任何失败都返回结构化 error，不抛出到框架层。"""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "问题不能为空", "stage": "input"}
    if len(q) > 1000:
        return {"ok": False, "error": "问题太长（上限 1000 字）", "stage": "input"}

    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": build_user_prompt(q, mode)},
    ]
    try:
        data = await raw.raw_completion(raw.RawCompletionRequest(
            model=model, messages=messages, max_tokens=512, temperature=0.2))
    except Exception as exc:  # noqa: BLE001 - 上游错误统一结构化
        return {"ok": False, "error": f"模型调用失败：{exc}", "stage": "generate"}

    demo = bool(data.get("demo"))
    actual_model = str(data.get("model") or "unknown")
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return {"ok": False, "error": "模型返回内容为空", "stage": "generate"}
    if not isinstance(content, str):
        return {"ok": False, "error": "模型返回格式异常", "stage": "generate"}

    try:
        parsed = parse_line_protocol(content)
    except ParseError as exc:
        # 行协议不合规：绝不猜测补齐，明确告知重试
        return {"ok": False, "error": f"模型输出不符合命令协议（{exc}），请换个说法重试",
                "stage": "parse", "raw_excerpt": content[:300]}

    # 逐条门控 + 逐条模拟：一条 DENY 不影响其它只读命令的展示
    command_items: list[dict[str, Any]] = []
    all_verdicts: list[Verdict] = []
    for line in parsed.commands:
        v = gate_check(line)
        all_verdicts.append(v)
        command_items.append({
            "text": line if v.level != "DENY" else "",
            "original": line,
            "risk": v.level,
            "blocked": v.level == "DENY",
            "reason": v.reason,
            "consequences": v.consequences,
            "alternatives": v.alternatives,
            "simulation": simulate_command(line, v, fs_path=fs_path),
        })

    # 整体风险取最高等级；整体命令文本只保留未被拦截的行（兼容旧前端）
    effective_lines = [it["text"] for it in command_items if it["text"]]
    effective_command = "\n".join(effective_lines)
    overall = max(all_verdicts, key=lambda v: LEVEL_ORDER[v.level])
    merged_alts: list[str] = []
    for v in all_verdicts:
        for alt in v.alternatives:
            if alt not in merged_alts:
                merged_alts.append(alt)

    # 整体模拟：把保留下来的命令合并推演，给前端一个总览卡片
    simulation = simulate_command(effective_command, overall, fs_path=fs_path) if effective_command else {
        "command": "", "status": "blocked", "head": "",
        "summary": "所有命令均命中安全规则，已全部拦截，无可模拟内容。",
        "effects": [], "writes_data": False, "network_access": False,
        "reversible": "不适用（已拦截）", "sample_output": "无", "warnings": [],
    }

    return {
        "ok": True,
        "query": q,
        "mode": mode,
        "explain": parsed.explain,
        "command": effective_command,
        "commands": command_items,
        "risk": overall.level,
        "model_risk": parsed.model_risk,
        "reason": overall.reason,
        "consequences": overall.consequences,
        "params": parsed.params,
        "alternatives": merged_alts,
        "unverified": any(v.unverified for v in all_verdicts),
        "simulation": simulation,
        "fs_context": (simulation.get("real_fs") or {}).get("display", "") if simulation else "",
        "model": actual_model,
        "demo": demo,
        "notice": "只生成 · 不执行 · 不隔离：本工具只展示命令文本与其后果模拟，绝不会替你运行任何命令。",
    }
