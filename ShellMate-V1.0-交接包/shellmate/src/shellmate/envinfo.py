"""环境探测：把「我在什么机器上」注入 Prompt。

这是 harness 层提升准确率最划算的一步——比换模型便宜得多：
发行版、shell、当前目录、是否 WSL、有无 GPU，都会改变该生成什么命令。
探测全部只读，失败一律降级为 unknown，不阻塞启动。
"""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EnvInfo:
    system: str
    distro: str
    shell: str
    shell_version: str
    cwd: str
    wsl: bool
    wsl_distro: str
    gpu: str
    python: str

    def prompt_block(self) -> str:
        """注入 System Prompt 的结构化环境事实。"""
        lines = [
            f"操作系统: {self.system} / {self.distro}",
            f"shell: {self.shell} {self.shell_version}".strip(),
            f"工作目录: {self.cwd}",
            f"运行环境: {'WSL2 (' + self.wsl_distro + ')' if self.wsl else '原生'}",
            f"GPU: {self.gpu}",
            "命令语法: bash（POSIX sh 兼容），不要生成 PowerShell 语法",
        ]
        return "\n".join(f"- {line}" for line in lines)

    def display_line(self) -> str:
        """启动横幅下面那一行，给用户看的。"""
        base = f"{self.distro} · {self.shell} {self.shell_version}".strip()
        gpu = f" · GPU {self.gpu}" if self.gpu != "unknown" else ""
        wsl = " · WSL2" if self.wsl else ""
        return f"环境已探测：{base} · cwd {self.cwd}{wsl}{gpu}"

    def is_windows_native(self) -> bool:
        return self.system == "Windows" and not self.wsl


def _read_os_release() -> str:
    path = Path("/etc/os-release")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return platform.platform()
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip().strip('"')
    return fields.get("PRETTY_NAME") or fields.get("ID") or platform.platform()


def _detect_wsl() -> tuple[bool, str]:
    distro = os.environ.get("WSL_DISTRO_NAME", "")
    if distro:
        return True, distro
    try:
        version = Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        version = ""
    if "microsoft" in version or "wsl" in version:
        return True, "WSL"
    return False, ""


def _detect_shell() -> tuple[str, str]:
    raw = os.environ.get("SHELL", "")
    name = Path(raw).name if raw else ("powershell" if platform.system() == "Windows" else "unknown")
    version = os.environ.get("BASH_VERSION", "")
    if version:
        version = version.split("(")[0].strip()
    elif os.environ.get("ZSH_VERSION"):
        version = os.environ["ZSH_VERSION"]
        name = "zsh"
    return name or "unknown", version


def _detect_gpu() -> str:
    """只读判断，不起任何子进程——本工程不允许存在执行路径。"""
    if shutil.which("nvidia-smi") or Path("/proc/driver/nvidia").exists():
        return "NVIDIA 驱动已安装"
    if Path("/dev/dri").exists():
        return "DRM 设备可用"
    return "unknown"


def detect() -> EnvInfo:
    system = platform.system()
    wsl, wsl_distro = _detect_wsl()
    shell, shell_version = _detect_shell()
    return EnvInfo(
        system=system,
        distro=("Windows" if system == "Windows" and not wsl else _read_os_release()),
        shell=shell,
        shell_version=shell_version,
        cwd=os.getcwd().replace("\\", "/"),
        wsl=wsl,
        wsl_distro=wsl_distro,
        gpu=_detect_gpu(),
        python=platform.python_version(),
    )
