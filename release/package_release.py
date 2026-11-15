"""打包 margin-console 交付物（在 workspace 根目录运行，不写入 fullstack 仓库）。

产出 release/ 下两个 zip：
- margin-console-v0.2.0-源码包.zip        跨平台、体积小、按 README 启动
- margin-console-v0.2.0-免安装运行包.zip   含 venv + 预构建前端 + start.bat，本机双击即用
"""
from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FS = ROOT / "fullstack"
OUT = ROOT / "release"
PREFIX = "margin-console-v0.2.0"

LEGACY_FILES = ["index.html", "app.js", "data.js", "engine.js", "styles.css"]

README_TXT = """余量 · FREE TIER ROUTER  v0.2.0  交付包
================================================

【两个包怎么选】

1) margin-console-v0.2.0-免安装运行包.zip   <- 想马上看效果，选这个
   - 已包含 Python 虚拟环境 (venv)、预构建前端 (web/dist)、示例配置 .env
   - 解压到不含中文和空格的路径，双击 start.bat
   - 浏览器会自动打开 http://127.0.0.1:8000/#guide （新手接入向导）
   - 关闭那个最小化的 "margin-console-server" 黑色窗口 = 停止服务
   - 仅保证在当前这台 Windows 机器可用（venv 绑定本机 Python 路径）
   - 若 8000 端口被占用，编辑 start.bat 里的 --port 换个端口

2) margin-console-v0.2.0-源码包.zip         <- 换机器 / 改代码 / 归档，选这个
   - 不含 venv 与 node_modules，体积小
   - 按 README.md「一键启动」：
       python -m venv venv
       venv\\Scripts\\pip install -r requirements-dev.txt
       copy .env.example .env
       venv\\Scripts\\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000
   - 已附带 web/dist 预构建前端，不装 Node 也能看到完整页面

【第一次打开先看什么】

首屏顶部黄色横幅 + 左侧导航第一项「新手接入向导」。
默认是「演示模式」：额度、趋势、对话回答都是本地模拟数据，零配置、不联网、不花钱，
不接入也能完整体验所有界面。

想用真实免费大模型，按向导四步：
   ① 看懂演示模式与接入后的区别
   ② 选一家平台（每家都有官方注册页 / API Key 页 / 免费政策页直达链接）
   ③ 粘贴自己的 API Key，点「测试连接」，通过后点「保存并启用」
   ④ 发一条真实消息，看到模型回复、真实模型名与延迟就说明接好了

保存后顶栏 DEMO 变成 LIVE，对话测试台自动走真实模型；可一键移除回到演示模式。
注意：免费模型都要用你自己的账号申请 Key，本工具不代注册、不生成密钥、不上传密钥。

【安全说明】

- 包内 .env 是示例配置，UPSTREAM_API_KEY 为占位符，不含任何真实密钥。
- 真实 API Key 只保存在你自己机器的 SQLite 中，页面与日志只显示脱敏值。
- 请勿把填过真实 Key 的 .env、data/*.db 上传到公开仓库。

【内容清单】

fullstack/ 全部源码、文档、截图、测试、评测指标、启动冒烟与交付审计
00-原版原型/      零依赖旧版前端原型 5 个文件
01-设计文档/      docs/ 设计说明、技术方案与 SOP 技能资料
docs/FREE_TIER_CATALOG.md   2026-11-13 官方核实的免费大模型政策清单（带官方链接、核实级别）
README.md 第一节  「给完全没用过的人：5 分钟上手」

【版本与验证】

v0.2.0 / commit b8a9d74 / tag v0.2.0
pytest 48 passed · 评测 24/24 100% · 启动冒烟 15 项 · 交付审计 31/31、0 FAIL / 0 WARN
已知局限见 EVAL_REPORT.md 第 6 节：额度表与 Usage 轮询仍是演示数据；
政策清单是静态快照、运行时不联网；未用真实厂商 Key 实测；Docker 未实机构建。
"""

START_BAT = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "venv\\Scripts\\python.exe" goto NOVENV
if not exist ".env" copy ".env.example" ".env" >nul
echo Starting margin-console ... a minimized server window will appear.
start "margin-console-server" /min "venv\\Scripts\\python.exe" -m uvicorn server.main:app --host 127.0.0.1 --port 8000
timeout /t 5 >nul
start "" "http://127.0.0.1:8000/#guide"
echo.
echo Opened http://127.0.0.1:8000/#guide
echo To STOP: close the minimized "margin-console-server" window.
echo.
pause
exit /b 0
:NOVENV
echo [ERROR] venv\\Scripts\\python.exe not found.
echo Use the source package instead, or follow README.md to create a venv.
pause
exit /b 1
"""


def tracked_files() -> list[str]:
    out = subprocess.check_output(["git", "ls-files"], cwd=FS, text=True)
    return [x.strip() for x in out.splitlines() if x.strip()]


def skip(rel: str) -> bool:
    parts = Path(rel).parts
    if any(p in {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
           for p in parts):
        return True
    if parts[0] in {".git", "logs", "venv", "node_modules"}:
        return True
    if parts[0] == "data" and parts[-1].endswith((".db", ".sqlite3")):
        return True
    if parts[-1] in {".env"} or parts[-1].endswith((".pyc", ".pyo", ".log", ".tsbuildinfo")):
        return True
    return False


def add_bytes(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    zf.writestr(zipfile.ZipInfo(name), data, compress_type=zipfile.ZIP_DEFLATED)


def build(name: str, with_runtime: bool) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / name
    if target.exists():
        target.unlink()

    files = [f for f in tracked_files() if not skip(f)]
    # web/dist 被 .gitignore 忽略，但预构建前端要随包交付（不装 Node 也能看页面）
    files += [str(p.relative_to(FS)).replace("\\", "/")
              for p in (FS / "web" / "dist").rglob("*") if p.is_file()]
    files = sorted(set(files))

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        add_bytes(zf, "先看我.txt", README_TXT.encode("utf-8-sig"))
        for rel in files:
            zf.write(FS / rel, rel)

        # 旧原型 + 设计文档（工作区根，不在 fullstack git 仓库内）
        for f in LEGACY_FILES:
            p = ROOT / f
            if p.is_file():
                zf.write(p, f"00-原版原型/{f}")
        for p in (ROOT / "docs").rglob("*"):
            if p.is_file():
                zf.write(p, f"01-设计文档/{p.relative_to(ROOT / 'docs')}")

        if with_runtime:
            add_bytes(zf, "start.bat", START_BAT.encode("ascii"))
            env_src = (FS / ".env.example").read_bytes()
            add_bytes(zf, ".env", env_src)
            for p in (FS / "venv").rglob("*"):
                if p.is_file() and "__pycache__" not in p.parts:
                    zf.write(p, str(p.relative_to(FS)).replace("\\", "/"))
    return target


def verify(path: Path, with_runtime: bool) -> None:
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        required = ["先看我.txt", "README.md", "EVAL_REPORT.md", "CHANGELOG.md",
                    "docs/FREE_TIER_CATALOG.md", "server/main.py",
                    "server/data/free_tier_catalog.json",
                    "server/data/connect_presets.json",
                    "web/dist/index.html", "docs/app-guide.png",
                    "00-原版原型/index.html", "01-设计文档/README.md"]
        if with_runtime:
            required += ["start.bat", ".env", "venv/Scripts/python.exe"]
        missing = [r for r in required if r not in names]
        assert not missing, f"{path.name} 缺文件: {missing}"
        assert not any(n.startswith(".git/") or n.endswith(".db")
                       or "/node_modules/" in n for n in names), "打进了不该有的文件"
        if with_runtime:
            assert b"YOUR_UPSTREAM_KEY_HERE" in zf.read(".env"), "示例配置应只含占位 Key"
        else:
            assert not any(n.startswith("venv/") for n in names), "源码包不应含 venv"
            assert zf.testzip() is None, "源码包 CRC 校验失败"
    print(f"[OK] {path.name}  {path.stat().st_size / 1024 / 1024:.2f} MB  "
          f"{len(names)} files")


def main() -> None:
    src = build(f"{PREFIX}-源码包.zip", False)
    run = build(f"{PREFIX}-免安装运行包.zip", True)
    with zipfile.ZipFile(src) as z:
        n1 = len(z.namelist())
    with zipfile.ZipFile(run) as z:
        n2 = len(z.namelist())
    verify(src, False)
    verify(run, True)
    if (ROOT / "package_release.py").exists():
        shutil.copyfile(ROOT / "package_release.py", OUT / "package_release.py")
    (OUT / "先看我.txt").write_bytes(README_TXT.encode("utf-8-sig"))
    print(f"\n源码包 {n1} 个文件；免安装包 {n2} 个文件")
    print(f"输出目录：{OUT}")


if __name__ == "__main__":
    main()
