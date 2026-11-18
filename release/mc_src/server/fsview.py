"""真实目录浏览（只读元数据）。

设计红线（与命令助手一脉相承）：
- **只读元数据**：仅用 os.scandir / entry.stat 取「名字 / 类型 / 大小 / 修改时间 / 权限位」，
  绝不 open 任何文件内容、绝不执行任何命令。
- **路径白名单**：只能浏览 ASSISTANT_FS_ROOTS 声明的逻辑根，realpath 必须仍在根内，
  ``..`` / 符号链接逃逸一律拒绝。
- **不跟随符号链接**：link 只登记为 type="link"，不展开、不 stat 其目标内容。
- **限深限宽**：单层最多 MAX_ENTRIES 条，避免一次扫描把界面/内存拖垮。
- **敏感文件过滤**：.env / *.db / *token* / *.key / id_rsa 等只按「已隐藏」计数，不返回名字。

环境变量：
    ASSISTANT_FS_ROOTS  形如 ``project=/hostfs/project,tmp=/hostfs/tmp``（容器内已只读挂载的路径）
    ASSISTANT_FS_PROJECT_DISPLAY / ASSISTANT_FS_TMP_DISPLAY  宿主侧展示路径前缀（默认 WSL 路径）
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

MAX_ENTRIES = 200
MAX_PATH_CHARS = 600

_DEFAULT_ROOTS = "project=/hostfs/project,tmp=/hostfs/tmp"
_DISPLAY = {
    "project": os.environ.get("ASSISTANT_FS_PROJECT_DISPLAY", "/root/safeshell-pro"),
    "tmp": os.environ.get("ASSISTANT_FS_TMP_DISPLAY", "/tmp"),
}

# 只隐藏「名字」，不读取内容；这些文件即使列出元数据也可能诱导用户复制敏感路径
_SENSITIVE_RE = re.compile(
    r"(^|\.)(env|gitignore)(\..*)?$|"
    r"\.(db|sqlite|sqlite3|key|pem|p12|pfx)$|"
    r"(^|[-_])(token|secret|password|credential|apikey|api_key)([-_]|$)|"
    r"^(id_rsa|id_ed25519|id_dsa|\.netrc|\.htpasswd)$",
    re.I,
)


class FsError(Exception):
    """路径非法 / 不在白名单 / 不可读。"""


def _parse_roots() -> dict[str, Path]:
    raw = os.environ.get("ASSISTANT_FS_ROOTS", _DEFAULT_ROOTS) or ""
    roots: dict[str, Path] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        label, _, p = item.partition("=")
        label, p = label.strip(), p.strip()
        if label and p:
            roots[label] = Path(p)
    return roots


def available_roots() -> list[dict[str, Any]]:
    """返回当前可见的逻辑根（挂载存在才是可用）。"""
    out = []
    for label, p in _parse_roots().items():
        exists = p.is_dir()
        out.append({
            "label": label,
            "display": _DISPLAY.get(label, str(p)),
            "available": exists,
        })
    return out


def resolve(path: str) -> tuple[Path, str, str, str]:
    """把 ``root/rel/path`` 解析成 (真实路径, root, 相对路径, 展示路径)。

    任何逃逸白名单根、超长、含 NUL 的路径都抛 FsError。
    """
    path = (path or "").strip().replace("\\", "/")
    if not path or "\x00" in path or len(path) > MAX_PATH_CHARS:
        raise FsError("路径为空、含非法字符或过长")
    roots = _parse_roots()
    if not roots:
        raise FsError("未配置可浏览目录（ASSISTANT_FS_ROOTS 为空）")

    parts = [x for x in path.split("/") if x not in ("", ".")]
    label = parts[0] if parts else next(iter(roots), "")
    if label not in roots:
        raise FsError(f"根目录 {label} 不在白名单内，可选：{', '.join(roots)}")
    rel_parts = []
    for seg in parts[1:]:
        if seg == "..":
            raise FsError("不允许使用 .. 回退目录")
        rel_parts.append(seg)

    base = roots[label].resolve()
    if not base.is_dir():
        raise FsError(f"目录根 {label} 未挂载或不可读（请检查只读卷挂载）")
    target = (base.joinpath(*rel_parts)).resolve()
    # 逃逸检查：解析后的真实路径必须仍在 base 之内
    if target != base and base not in target.parents:
        raise FsError("路径越界（符号链接或 .. 指向白名单之外）")

    rel = "/".join(rel_parts)
    display_prefix = _DISPLAY.get(label, str(base))
    display = display_prefix + ("/" + rel if rel else "")
    return target, label, rel, display


def list_dir(path: str) -> dict[str, Any]:
    """列出单层目录的元数据（只读，不读内容，不跟随软链）。"""
    target, label, rel, display = resolve(path)
    entries: list[dict[str, Any]] = []
    hidden = 0
    truncated = False
    try:
        with os.scandir(target) as it:
            for e in it:
                if _SENSITIVE_RE.search(e.name):
                    hidden += 1
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                    if e.is_symlink():
                        kind = "link"
                    elif e.is_dir(follow_symlinks=False):
                        kind = "dir"
                    else:
                        kind = "file"
                    entries.append({
                        "name": e.name,
                        "type": kind,
                        "size": int(st.st_size) if kind != "dir" else 0,
                        "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                        "mtime_ts": st.st_mtime,
                        "mode": oct(st.st_mode & 0o777)[2:],
                    })
                except OSError:
                    # 文件在扫描瞬间被删/无权限：跳过，不让它影响整棵树
                    continue
                if len(entries) >= MAX_ENTRIES:
                    truncated = True
                    break
    except PermissionError:
        raise FsError("没有权限读取该目录")
    except FileNotFoundError:
        raise FsError("目录不存在")
    entries.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    return {
        "ok": True,
        "path": f"{label}/{rel}" if rel else label,
        "display": display or "/",
        "entries": entries,
        "hidden": hidden,
        "truncated": truncated,
        "available_roots": available_roots(),
        "note": "仅读取文件名/类型/大小/修改时间等元信息，不读取文件内容、不执行任何命令。",
    }


def snapshot(path: str, limit: int = MAX_ENTRIES) -> dict[str, Any]:
    """给模拟引擎用的一层目录快照：文件列表 + 汇总（只读元数据）。

    返回 ``{"ok":..., "display":..., "files":[{name,type,size,mtime}], "dirs":[...],
    "count":..., "total_bytes":..., "note":...}``；失败返回 ``ok=False`` 并带原因，
    由调用方优雅降级为抽象示例。
    """
    try:
        info = list_dir(path)
    except FsError as exc:
        return {"ok": False, "error": str(exc), "files": [], "dirs": []}
    files = [e for e in info["entries"] if e["type"] == "file"]
    dirs = [e for e in info["entries"] if e["type"] == "dir"]
    return {
        "ok": True,
        "path": info["path"],
        "display": info["display"],
        "files": files[:limit],
        "dirs": dirs[:limit],
        "count": len(files) + len(dirs),
        "total_bytes": sum(e["size"] for e in files),
        "truncated": info["truncated"],
        "note": "基于真实目录元数据，不带文件内容。",
    }
