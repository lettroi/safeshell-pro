"""真实目录浏览与「基于真实文件的预测」测试。

红线验证：
- 只读元数据、不读文件内容、不跟随软链、路径白名单防逃逸；
- 预测引擎纯计算，绝不执行任何命令。
"""
from __future__ import annotations

import ast
import os

import pytest
from fastapi.testclient import TestClient

from server import assistant, fsview
from server.main import app

client = TestClient(app)


@pytest.fixture
def browse_root(tmp_path, monkeypatch):
    """构造一个带真实文件的可浏览根，并让 fsview 只认它。"""
    (tmp_path / "report.txt").write_text("TOP SECRET CONTENT SHOULD NEVER LEAK", encoding="utf-8")
    (tmp_path / "app.log").write_text("error line", encoding="utf-8")
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.txt").write_text("deep", encoding="utf-8")
    (tmp_path / ".env").write_text("KEY=secret", encoding="utf-8")
    (tmp_path / "margin.db").write_text("binary", encoding="utf-8")
    monkeypatch.setenv("ASSISTANT_FS_ROOTS", f"proj={tmp_path}")
    return tmp_path


# ---- 目录树只读 ----
def test_list_dir_returns_metadata_only(browse_root):
    d = fsview.list_dir("proj")
    assert d["ok"] is True
    names = [e["name"] for e in d["entries"]]
    assert "report.txt" in names and "sub" in names
    app = next(e for e in d["entries"] if e["name"] == "app.log")
    assert app["type"] == "file" and app["size"] == 10
    sub = next(e for e in d["entries"] if e["name"] == "sub")
    assert sub["type"] == "dir"


def test_list_dir_never_reads_file_content(browse_root):
    d = fsview.list_dir("proj")
    blob = repr(d)
    assert "TOP SECRET" not in blob and "KEY=secret" not in blob


def test_sensitive_files_hidden(browse_root):
    d = fsview.list_dir("proj")
    names = [e["name"] for e in d["entries"]]
    assert ".env" not in names and "margin.db" not in names
    assert d["hidden"] >= 2


def test_path_escape_rejected(browse_root):
    with pytest.raises(fsview.FsError):
        fsview.resolve("proj/../../etc")
    with pytest.raises(fsview.FsError):
        fsview.resolve("nope/x")  # 不在白名单根


def test_symlink_not_followed(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    os.symlink(str(outside), str(root / "linkout"))
    monkeypatch.setenv("ASSISTANT_FS_ROOTS", f"proj={root}")
    d = fsview.list_dir("proj")
    link = next(e for e in d["entries"] if e["name"] == "linkout")
    assert link["type"] == "link"
    # 不能通过软链读到 outside 里的内容
    with pytest.raises(fsview.FsError):
        fsview.resolve("proj/linkout/secret.txt")


def test_root_display_no_double_slash(tmp_path, monkeypatch):
    """root 逻辑根的展示前缀是 /，拼 rel 不能产出 //etc。"""
    (tmp_path / "etc").mkdir()
    monkeypatch.setenv("ASSISTANT_FS_ROOTS", f"root={tmp_path}")
    monkeypatch.setitem(fsview._DISPLAY, "root", "/")
    _, _, _, display = fsview.resolve("root/etc")
    assert display == "/etc", f"根下路径不应有双斜杠，实际 {display}"
    _, _, _, display_root = fsview.resolve("root")
    assert display_root == "/"


def test_fs_endpoint_ok_and_error(browse_root):
    r = client.get("/api/v1/assistant/fs", params={"path": "proj"})
    assert r.status_code == 200 and r.json()["ok"] is True
    bad = client.get("/api/v1/assistant/fs", params={"path": "proj/../../etc"})
    assert bad.status_code == 200 and bad.json()["ok"] is False


# ---- 基于真实文件的预测 ----
def test_snapshot_and_concrete_preview_match_real_names(browse_root):
    snap = fsview.snapshot("proj")
    assert snap["ok"] is True and snap["count"] >= 4
    preview = assistant._concrete_preview("ls -la", snap)
    names = [m["name"] for m in preview["matched"]]
    assert "report.txt" in names and "app.log" in names
    assert all("SECRET" not in repr(m) for m in preview["matched"])


def test_concrete_preview_name_filter_and_size(browse_root):
    snap = fsview.snapshot("proj")
    preview = assistant._concrete_preview("find . -name '*.log'", snap)
    assert [m["name"] for m in preview["matched"]] == ["app.log"]
    big = assistant._concrete_preview("find . -size +1M", snap)
    assert big["matched"] == []  # 真实文件都远小于 1M，不臆造


def test_concrete_preview_ls_lt_sorts_by_mtime(browse_root):
    """ls -lt 的组合 flag -lt 必须被识别为按时间排序，且输出带修改时间。"""
    import time as _t
    # 让 report.txt 比 app.log 新
    base = _t.time() - 10000
    import os as _os
    _os.utime(browse_root / "app.log", (base, base))
    snap = fsview.snapshot("proj")
    preview = assistant._concrete_preview("ls -lt | head -n 5", snap)
    names = [m["name"] for m in preview["matched"]]
    # 被改成 10000 秒前的 app.log 必须排在刚创建的 report.txt/notes.md 之后
    assert "app.log" in names
    assert names.index("app.log") > names.index("report.txt"), f"应按时间倒序，实际 {names}"
    assert all(m["mtime"] and "-" in m["mtime"] for m in preview["matched"])


def test_concrete_preview_delete_missing_target(browse_root):
    snap = fsview.snapshot("proj")
    preview = assistant._concrete_preview("rm /tmp/no_such_file_xyz.log", snap)
    assert preview["exists"] is False
    assert "没有" in preview["hint"]


def test_simulate_with_fs_path_returns_real_fs(browse_root):
    s = assistant.simulate_command("ls -la", fs_path="proj")
    assert s["status"] == "simulated"
    assert s["real_fs"] and s["real_fs"]["available"] is True
    assert any(m["name"] == "report.txt" for m in s["real_fs"]["matched"])
    assert "基于真实文件" in s["sample_output"]


def test_simulate_without_fs_path_still_abstract():
    s = assistant.simulate_command("ls -la")
    assert s["status"] == "simulated"
    # 未传 fs_path 时必须优雅降级为抽象示例，不影响可用性
    assert s.get("real_fs") is None


def test_generate_endpoint_accepts_fs_path(browse_root):
    # 不依赖真实上游：空查询在入口被拦，验证参数被接受即可
    r = client.post("/api/v1/assistant/generate", json={"query": "", "fs_path": "proj"})
    assert r.status_code == 200 and r.json()["ok"] is False


# ---- 零执行原语（AST，文档/注释不算） ----
def test_fs_modules_no_exec_primitives():
    banned_calls = {"system", "popen", "spawn", "spawnl", "spawnv", "execv", "execve", "fork", "Popen", "run"}
    for mod in (assistant, fsview):
        tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
        imported = set()
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)
        assert not ({"subprocess", "commands", "pty"} & imported), f"{mod.__name__} 引入执行模块"
        assert not (banned_calls & called), f"{mod.__name__} 调用执行原语: {banned_calls & called}"
        # 不得调用内建 open()（读文件内容）——用 AST 判断，文档/注释里的字样不算
        builtin_calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                builtin_calls.add(node.func.id)
        assert "open" not in builtin_calls, f"{mod.__name__} 调用了 open() 读取文件内容"
        src = open(mod.__file__, encoding="utf-8").read()
        assert "eval(" not in src and "exec(" not in src
