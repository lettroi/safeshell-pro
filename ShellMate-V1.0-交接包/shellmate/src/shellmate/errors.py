"""错误类型与面向小白的话术。

原则：不静默失败。每一种错误都带一条「接下来该做什么」的具体指引，
不出现「请联系管理员」这类把用户推开的话——本地工具的用户自己就是管理员。
"""

from __future__ import annotations


class ShellMateError(Exception):
    """基类。所有错误都必须带可直接照做的指引。"""

    hint: str = ""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if hint is not None:
            self.hint = hint

    def render(self) -> str:
        if self.hint:
            return f"{self.message}\n下一步：{self.hint}"
        return self.message


class ModelUnavailableError(ShellMateError):
    """本机 Ollama 没启动 / 连不上。

    云端兜底场景会用 prefix / hint 覆盖话术，避免把「本机没装 Ollama」的指引
    错误地显示给正在走免费云端模型的用户。
    """

    hint = (
        "在另一个终端执行  ollama serve  （保持窗口别关），"
        "首次使用先执行  ollama pull qwen3:8b  ，然后回到这里重新提问。"
    )
    CLOUD_HINT = (
        "已自动尝试其他免费模型；若仍失败，请确认网关在运行、团队控制台里模型未被停用，"
        "或稍后重试（免费档可能瞬时过载）。"
    )

    def __init__(self, detail: str = "", *, prefix: str = "连不上本机模型服务",
                 hint: str | None = None) -> None:
        msg = prefix + (f"（{detail}）" if detail else "")
        super().__init__(msg, self.hint if hint is None else hint)


class ModelTimeoutError(ShellMateError):
    hint = "按 Esc 可以中断；也可以换更小的模型，或检查是不是有别的程序占满了显存。"

    def __init__(self, seconds: int) -> None:
        super().__init__(f"模型 {seconds} 秒没有返回", self.hint)


class ParseError(ShellMateError):
    """结构化输出解析失败：必须重问，不猜测、不降级成自由文本。"""

    hint = "我已丢弃这次输出，请再说一遍问题（换个说法通常就好）。"

    def __init__(self, detail: str = "") -> None:
        msg = "这次返回的格式不完整，没有生成命令" + (f"（{detail}）" if detail else "")
        super().__init__(msg, self.hint)


class CloudDisabledError(ShellMateError):
    hint = (
        "如果确实要试用线上模型，在输入框敲  enable-cloud  并按提示确认；"
        "关闭用  disable-cloud 。不开也能正常使用全部功能。"
    )

    def __init__(self) -> None:
        super().__init__("线上模型模式当前是关闭的", self.hint)


class UnsupportedShellError(ShellMateError):
    hint = "请在 WSL2 的 bash/zsh 里运行 ShellMate；生成的命令以 bash 语法为准。"

    def __init__(self, shell: str) -> None:
        super().__init__(f"检测到 {shell}，暂不按该 shell 生成命令", self.hint)
