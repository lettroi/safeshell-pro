"""静态风险提示（参数级规则引擎）——本工程的安全核心。

明确命名：这里没有沙箱，没有任何执行或隔离。它只是对「命令文本」做确定性检查，
并在界面上写明「只生成 · 不执行 · 不隔离」，避免给用户虚假的安全感。

三条原则：
1. 危险来自参数组合，不是命令名。`rm` 必然在白名单里，但 `rm -rf /var` 必须被拒绝。
2. 模型填的 risk_level 只是参考，这里覆盖成确定性结论。
3. 白名单只用于标注「未验证命令」，绝不承担拦截职责。
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from .client import CommandResult

LEVEL_ORDER = {"SAFE": 0, "CAUTION": 1, "DENY": 2}

# 常见命令清单：只用于「未验证」标注与幻觉率统计，不是准入名单。
KNOWN_COMMANDS: frozenset[str] = frozenset(
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
hostnamectl localectl resolvectl openssl ssh scp rsync true false
""".split()
)

SYSTEM_PATHS: frozenset[str] = frozenset(
    """/ /var /etc /usr /bin /sbin /boot /dev /sys /proc /lib /lib64 /opt /root
/home /srv /run /mnt /media""".split()
)

PERMISSIVE_MODES = {"777", "666", "000", "a+rwx", "o+rwx", "0777", "a=rwx"}

# 已知长选项：用于评测脚本估算「幻觉率」（模型编造不存在的参数）。
KNOWN_OPTIONS: dict[str, frozenset[str]] = {
    "find": frozenset("name iname type size mtime ctime atime maxdepth mindepth path perm user group empty exec ok delete print print0 newermt regex iregex".split()),
    "ls": frozenset("all almost-all long human-readable reverse time sort size directory recursive inode color hide".split()),
    "grep": frozenset("ignore-case recursive line-number invert-match count word-regexp extended-regexp fixed-strings only-matching color files-with-matches exclude include context after-context before-context".split()),
    "tar": frozenset("create extract list file verbose gzip bzip2 xz directory exclude append delete compare keep-old-files strip-components".split()),
    "ps": frozenset("all user aux forest sort pid".split()),
    "chmod": frozenset("recursive reference verbose changes quiet".split()),
    "rm": frozenset("recursive force interactive verbose dir one-file-system preserve-root no-preserve-root".split()),
    "du": frozenset("summarize human-readable max-depth all total apparent-size".split()),
    "df": frozenset("human-readable inode total type".split()),
    "curl": frozenset("header location output remote-name request data user-agent silent fail verbose insecure".split()),
    "sort": frozenset("human-numeric-sort numeric reverse key unique output field-separator".split()),
    "head": frozenset("lines bytes quiet verbose".split()),
    "tail": frozenset("lines bytes follow quiet verbose pid sleep-interval".split()),
    "git": frozenset("version help force hard all amend oneline graph stat patch quiet verbose force-with-lease no-verify".split()),
    "ss": frozenset("tcp udp listening numeric processes all extended info".split()),
}

_SPLIT_RE = re.compile(r"(?:\|\||&&|;|\||\n)")
_SUBSHELL_RE = re.compile(r"\$\(|`|>\s*/dev/(?:sd|nvme|hd|mmcblk)|:\(\)\s*\{")
_BLOCK_DEVICE_RE = re.compile(r"^/dev/(?:sd|nvme|hd|mmcblk|loop|vd)")
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


@dataclass
class GateVerdict:
    level: str = "SAFE"
    matched_rules: list[str] = field(default_factory=list)
    unverified: bool = False
    segments_checked: int = 0
    reason: str = ""
    consequences: str = ""
    alternatives: list[str] = field(default_factory=list)
    blocked_command: str = ""


@dataclass(frozen=True)
class Rule:
    rule_id: str
    level: str
    description: str
    consequences: str = ""
    alternatives: tuple[str, ...] = ()


def _paths(tokens: list[str]) -> list[str]:
    """取出看起来像路径的参数（排除选项与明显的选项值）。"""
    out: list[str] = []
    for i, tok in enumerate(tokens[1:], start=1):
        if tok.startswith("-") and i > 0 and tokens[i - 1] not in {"-size", "-name"}:
            continue
        if i > 0 and tokens[i - 1] in {"-C", "-f", "-o", "-t", "-d", "-path", "-type"}:
            continue
        if "/" in tok or tok in {".", "..", "~"} or tok.startswith("$") or tok.startswith("*"):
            out.append(tok)
    return out


def _is_system_path(token: str) -> bool:
    t = token.strip().strip("'\"").rstrip("*").rstrip("/") or "/"
    if t in SYSTEM_PATHS or t in {"~", "$HOME", "${HOME}"}:
        return True
    return any(t.startswith(p + "/") for p in SYSTEM_PATHS if p != "/")


def _flags(tokens: list[str]) -> set[str]:
    flags: set[str] = set()
    for tok in tokens[1:]:
        if tok.startswith("--"):
            flags.add(tok.split("=", 1)[0])
        elif tok.startswith("-") and len(tok) > 1 and not tok[1].isdigit():
            flags.update("-" + ch for ch in tok[1:])
    return flags


def _recursive(tokens: list[str]) -> bool:
    return bool(_flags(tokens) & {"-r", "-R", "--recursive"})


def _forced(tokens: list[str]) -> bool:
    return bool(_flags(tokens) & {"-f", "--force"})


def _strip_prefixes(tokens: list[str]) -> tuple[list[str], bool]:
    """剥掉环境变量赋值、env、sudo 前缀，让规则看到真正的命令。"""
    rest = list(tokens)
    while rest and _ENV_ASSIGN_RE.match(rest[0]):
        rest.pop(0)
    if rest and rest[0] == "env":
        rest.pop(0)
    privileged = bool(rest and rest[0] in {"sudo", "su"})
    if privileged:
        rest = rest[1:]
    return rest, privileged


_CONS_VAR = (
    "/var 存放系统日志、缓存、包管理数据库与部分服务数据。整目录删除后通常无法恢复，"
    "机器可能无法重启或服务全部失效。"
)
_CONS_GENERIC_DELETE = "删除不可恢复，终端里没有回收站；路径写错或变量为空时，影响范围可能远超预期。"
_CONS_777 = "这会让本机任何账号都能改写这些文件；若目录位于共享目录或 Web 根目录下，风险更高。"

_ALT_DELETE = (
    "du -sh /var/* | sort -h",
    "mv 到临时目录观察几天，确认无用后再处理",
    "删除前先做一份备份",
)
_ALT_777 = ("chmod -R 755 ./project",)


def _r_delete_system(tokens: list[str], raw: str) -> bool:
    if not tokens or tokens[0] not in {"rm", "rmdir", "shred"}:
        return False
    if not (_recursive(tokens) or _forced(tokens) or tokens[0] == "shred"):
        return False
    return any(_is_system_path(p) or p.startswith("$") for p in _paths(tokens))


def _r_block_device(tokens: list[str], raw: str) -> bool:
    if tokens and tokens[0] in {"dd", "mkfs", "mkfs.ext4", "mkfs.xfs", "shred"}:
        if tokens[0] == "dd":
            return any(tok.startswith("of=") and _BLOCK_DEVICE_RE.match(tok[3:]) for tok in tokens)
        return any(_BLOCK_DEVICE_RE.match(p) for p in _paths(tokens))
    return bool(_BLOCK_DEVICE_RE.search(raw) and ">" in raw)


def _r_remote_pipe_shell(tokens: list[str], raw: str) -> bool:
    return bool(re.search(r"(?:curl|wget)[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b", raw, re.I))


def _r_fork_bomb(tokens: list[str], raw: str) -> bool:
    return bool(re.search(r":\(\)\s*\{.*\|\s*:\s*&", raw))


def _r_overwrite_system(tokens: list[str], raw: str) -> bool:
    return bool(re.search(r">\s*(?:/etc|/usr|/boot|/var)/", raw))


def _r_recursive_777(tokens: list[str], raw: str) -> bool:
    if not tokens or tokens[0] not in {"chmod", "chown"}:
        return False
    return _recursive(tokens) and any(tok.strip("'\"") in PERMISSIVE_MODES for tok in tokens[1:])


def _r_any_delete(tokens: list[str], raw: str) -> bool:
    """普通删除同样不可逆，至少 CAUTION；系统级路径由 DENY 规则接管。"""
    return bool(tokens and tokens[0] in {"rm", "rmdir", "unlink", "shred"})


def _r_destructive_git(tokens: list[str], raw: str) -> bool:
    if not tokens or tokens[0] != "git":
        return False
    if {"--hard", "-f", "--force"} & _flags(tokens):
        return True
    return len(tokens) > 2 and tokens[1] in {"reset", "clean", "push"}


def _r_find_delete(tokens: list[str], raw: str) -> bool:
    return bool(tokens and tokens[0] == "find" and ("-delete" in tokens or "-exec" in tokens))


def _r_service_stop(tokens: list[str], raw: str) -> bool:
    return bool(
        tokens
        and tokens[0] in {"systemctl", "service", "kill", "pkill"}
        and any(t in {"stop", "disable", "restart", "-9", "--force"} for t in tokens[1:])
    )


def _r_write_redirect(tokens: list[str], raw: str) -> bool:
    # 排除 -> 与 => 这类文本，避免把普通字符串误判成重定向
    return bool(re.search(r"(?<![\d=\-])>\s*[^\s]", raw)) and not raw.strip().startswith("#")


RULES: tuple[Rule, ...] = (
    Rule("DENY-DELETE-SYSTEM", "DENY", "递归/强制删除 + 系统级路径", _CONS_VAR, _ALT_DELETE),
    Rule("DENY-BLOCK-DEVICE", "DENY", "直接写入块设备", "会覆盖磁盘或分区上的原始数据，几乎不可能恢复。", ("先确认设备名：lsblk", "操作前做完整镜像备份")),
    Rule("DENY-REMOTE-EXEC", "DENY", "下载内容直接管道给 shell 执行", "会在本机执行一段你还没看过的脚本，内容随时可变，无法预先判断后果。", ("curl -o script.sh <URL> 下载后先读一遍再决定",)),
    Rule("DENY-FORK-BOMB", "DENY", "疑似 fork bomb", "会瞬间耗尽进程配额导致机器卡死，通常需要强制重启。", ()),
    Rule("DENY-OVERWRITE-SYSTEM", "DENY", "重定向覆盖系统目录文件", "会直接覆盖系统配置或服务文件，可能导致无法启动。", ("先备份原文件：cp -a <文件> <文件>.bak",)),
    Rule("CAUTION-777", "CAUTION", "递归改权限 + 777 等过度开放模式", _CONS_777, _ALT_777),
    Rule("CAUTION-DELETE", "CAUTION", "删除类命令（不可逆）", _CONS_GENERIC_DELETE, ("mv 到临时目录观察几天再处理", "删除前先做备份")),
    Rule("CAUTION-GIT-DESTRUCTIVE", "CAUTION", "git 强制/丢弃改动类操作", "可能丢弃未提交的工作区改动，且不易恢复。", ("先运行 git status 看清改动", "用 git stash 暂存而不是 --hard")),
    Rule("CAUTION-FIND-DELETE", "CAUTION", "find 带 -delete / -exec", "匹配范围稍写错就会批量删除，且不可恢复。", ("先去掉 -delete，只加 -print 看一遍匹配结果",)),
    Rule("CAUTION-SERVICE", "CAUTION", "停止/重启服务或强杀进程", "可能中断正在运行的服务或丢失未保存数据。", ("先用 ps / systemctl status 确认目标",)),
    Rule("CAUTION-REDIRECT", "CAUTION", "输出重定向写入文件", "会覆盖目标文件的原有内容。", ("把 > 换成 >> 追加，或先备份目标文件",)),
)

RULE_FUNCS = (
    _r_delete_system, _r_block_device, _r_remote_pipe_shell, _r_fork_bomb,
    _r_overwrite_system, _r_recursive_777, _r_any_delete,
    _r_destructive_git, _r_find_delete, _r_service_stop, _r_write_redirect,
)

# 这些模式跨管道/分号，必须在拆分前对整条命令再查一遍
GLOBAL_RULE_IDS = frozenset({"DENY-REMOTE-EXEC", "DENY-FORK-BOMB", "DENY-OVERWRITE-SYSTEM"})


def split_segments(command: str) -> list[str]:
    """按 shell 元字符拆分复合命令，逐段检查（管道左侧也不能漏）。"""
    return [p.strip() for p in _SPLIT_RE.split(command) if p.strip()]


def _record(verdict: GateVerdict, rule: Rule) -> None:
    if rule.rule_id not in verdict.matched_rules:
        verdict.matched_rules.append(rule.rule_id)
    if LEVEL_ORDER[rule.level] > LEVEL_ORDER[verdict.level]:
        verdict.level = rule.level
    if rule.consequences and not verdict.consequences:
        verdict.consequences = rule.consequences
    for alt in rule.alternatives:
        if alt not in verdict.alternatives:
            verdict.alternatives.append(alt)


def _record_caution(verdict: GateVerdict, rule_id: str, reason: str) -> None:
    if rule_id not in verdict.matched_rules:
        verdict.matched_rules.append(rule_id)
    if LEVEL_ORDER[verdict.level] < 1:
        verdict.level = "CAUTION"
    if not verdict.consequences:
        verdict.consequences = reason


def check(command: str) -> GateVerdict:
    """对一条命令文本做确定性检查。纯函数，无 IO、无执行。"""
    verdict = GateVerdict()
    if not command.strip():
        _record_caution(verdict, "CAUTION-EMPTY", "没有拿到可检查的命令文本。")
        verdict.reason = "空命令"
        return verdict

    if _SUBSHELL_RE.search(command):
        _record_caution(verdict, "CAUTION-SUBSHELL", "含命令替换或重定向到设备，执行范围不易一眼看清。")

    # 跨管道/分号的模式：用整条原文判定，避免拆分后丢失上下文
    for rule, func in zip(RULES, RULE_FUNCS):
        if rule.rule_id in GLOBAL_RULE_IDS and func([], command):
            _record(verdict, rule)

    for segment in split_segments(command):
        verdict.segments_checked += 1
        try:
            raw_tokens = shlex.split(segment, posix=True)
        except ValueError:
            _record_caution(verdict, "CAUTION-UNPARSEABLE", "引号没有闭合，命令结构无法可靠解析。")
            verdict.unverified = True
            continue
        if not raw_tokens:
            continue
        tokens, privileged = _strip_prefixes(raw_tokens)
        if privileged:
            _record_caution(verdict, "CAUTION-SUDO", "提权后误操作的影响范围会放大到整个系统。")
            verdict.alternatives.append("先不加 sudo 试一次，确认确实需要再提权")
        head = tokens[0] if tokens else ""
        if head and head not in KNOWN_COMMANDS:
            verdict.unverified = True
        if not tokens:
            continue
        for rule, func in zip(RULES, RULE_FUNCS):
            try:
                hit = func(tokens, segment)
            except (IndexError, AttributeError, TypeError):
                hit = False
            if hit:
                _record(verdict, rule)

    if verdict.level == "SAFE":
        verdict.reason = "未命中写入/删除/提权类规则，按只读命令处理"
    elif verdict.level == "CAUTION":
        verdict.reason = "命令可用，但改动范围或权限开放程度需要你先确认"
    else:
        verdict.reason = "命中不可逆规则，已拒绝输出命令"
    verdict.blocked_command = command
    return verdict


def apply_gate(result: CommandResult) -> CommandResult:
    """用确定性结论覆盖模型自评；DENY 时清空命令，确保任何渲染路径都不会输出它。"""
    verdict = check(result.command)
    result.matched_rules = verdict.matched_rules
    result.unverified = verdict.unverified
    result.alternatives = verdict.alternatives
    result.consequences = verdict.consequences
    result.risk_level = verdict.level
    result.reason = verdict.reason
    if verdict.level == "DENY":
        result.command = ""
    return result


def detect_hallucinated_options(command: str) -> list[str]:
    """评测用：找出命令中不在已知选项表里的 --long-option。

    只作为「疑似幻觉」统计口径，不参与拦截——表不全，误报必须由人复核。
    """
    found: list[str] = []
    for segment in split_segments(command):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            continue
        if not tokens:
            continue
        allowed = KNOWN_OPTIONS.get(tokens[0])
        if allowed is None:
            continue
        for tok in tokens[1:]:
            if tok.startswith("--"):
                name = tok.split("=", 1)[0][2:]
                if name and name not in allowed:
                    found.append(f"{tokens[0]}:{tok}")
    return found
