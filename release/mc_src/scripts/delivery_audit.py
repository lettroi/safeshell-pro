#!/usr/bin/env python3
"""五阶段交付物审计（S1-S5）。

用法：
    python delivery_audit.py --project /path/to/project
    python delivery_audit.py --project . --stage 3
    python delivery_audit.py --project . --json --out audit_report.json

纯 Python 标准库，无第三方依赖。检查项对应 references/06-pitfalls-and-gates.md
中带 [auto] 的 Definition of Done 条目。脚本只做形式合规检查，
内容是否真的合格仍需按 references 人工复核。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

STAGE_NAMES = {
    1: "S1 产品定义与原型",
    2: "S2 工程环境与基础搭建",
    3: "S3 后端开发与 Agent",
    4: "S4 测试与质量保障",
    5: "S5 交付与运维文档",
}

ICON = {"pass": "[PASS]", "fail": "[FAIL]", "warn": "[WARN]"}
SKIP_DIRS = {".git", "venv", ".venv", "env", "node_modules", "__pycache__",
             ".pytest_cache", "dist", "build", "site-packages"}


@dataclass
class Check:
    sid: str
    stage: int
    title: str
    status: str          # pass / fail / warn
    detail: str
    fix: str = ""


CHECKS: list[Check] = []


def add(sid: str, stage: int, title: str, ok, detail_ok: str, detail_bad: str,
        fix: str = "", warn: bool = False) -> None:
    status = "pass" if ok else ("warn" if warn else "fail")
    CHECKS.append(Check(sid, stage, title, status,
                        detail_ok if ok else detail_bad, fix if not ok else ""))


def first_existing(root: Path, candidates: list[str]) -> Path | None:
    for name in candidates:
        p = root / name
        if p.is_file():
            return p
    return None


def read_text(p: Path | None) -> str:
    if not p or not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def py_files(root: Path, limit: int = 300) -> list[Path]:
    out: list[Path] = []
    for p in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.stat().st_size > 400_000:
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return out


def corpus(root: Path) -> str:
    return "\n".join(read_text(p) for p in py_files(root))


def has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.I | re.M) is not None


def env_is_tracked(root: Path) -> bool | None:
    """True=被 git 跟踪（违规）；False=未跟踪；None=无法判定。"""
    try:
        r = subprocess.run(["git", "ls-files", "--error-unmatch", ".env"],
                           cwd=str(root), capture_output=True, text=True, timeout=8)
        if r.returncode == 0 and r.stdout.strip():
            return True
        if r.returncode == 1:
            return False
        return None
    except (OSError, subprocess.SubprocessError):
        return None


def check_s1(root: Path) -> None:
    prd = first_existing(root, ["PRD.md", "docs/PRD.md", "docs/prd.md", "PRD_v0.1.md",
                                "docs/PRD_v0.1.md", "产品需求文档.md"])
    add("S1-01", 1, "PRD 文档存在", prd is not None,
        f"找到 {prd.relative_to(root)}" if prd else "",
        "未找到 PRD.md / docs/PRD.md",
        "复制 assets/PRD_TEMPLATE.md 到项目根目录并按字段填写。")

    prd_text = read_text(prd)
    required = {"persona": r"用户画像|Persona", "pain": r"痛点|Pain Point",
                "story": r"用户故事|User Story", "mvp": r"MVP|Must-have",
                "nfr": r"非功能|NFR|TTFT"}
    missing = [k for k, pat in required.items() if not has(pat, prd_text)]
    add("S1-02", 1, "PRD 五类关键字段齐全", not missing,
        "Persona / Pain Point / 用户故事 / MVP / NFR 均可检出",
        "缺少字段：" + ", ".join(missing),
        "按 references/01-product-design.md 补齐，每条用户故事配 Given-When-Then。", warn=True)

    quantified = has(r"\d+\s*%|p95|TTFT|<\s*\d+\s*s|token", prd_text)
    add("S1-03", 1, "NFR 含可测数值", quantified,
        "检出百分比 / p95 / TTFT / token 等量化口径",
        "未检出量化数值，指标可能仍是形容词",
        "把「快/准/省」转成数值 + 测量方法，见 references/01。", warn=True)

    proto = root / "docs" / "prototypes"
    found = []
    if proto.is_dir():
        for key in ("normal", "loading", "error"):
            if any(key in p.name.lower() for p in proto.iterdir()):
                found.append(key)
    add("S1-04", 1, "三态原型截图齐全", len(found) == 3,
        f"docs/prototypes/ 命中：{', '.join(found)}",
        f"docs/prototypes/ 仅命中 {', '.join(found) or '无'}，缺 {', '.join(set(('normal','loading','error')) - set(found))}",
        "补齐 prototype-normal / prototype-loading / prototype-error 三张图。")

    readme = read_text(first_existing(root, ["README.md"]))
    add("S1-05", 1, "README 引用原型截图", "prototype-" in readme or "prototypes/" in readme,
        "README 已引用原型", "README 未引用三态原型",
        "在 README 加三张截图相对路径链接。", warn=True)


def check_s2(root: Path) -> None:
    req = first_existing(root, ["requirements.txt", "requirements.lock"])
    pyproject = first_existing(root, ["pyproject.toml"])
    add("S2-01", 2, "依赖清单存在", req is not None or pyproject is not None,
        f"找到 {(req or pyproject).relative_to(root)}" if (req or pyproject) else "",
        "未找到 requirements.txt / pyproject.toml",
        "复制 assets/requirements.example.txt 并锁版本。")

    if req:
        lines = [ln.strip() for ln in read_text(req).splitlines()
                 if ln.strip() and not ln.strip().startswith(("#", "-", "--"))]
        unlocked = [ln for ln in lines if "==" not in ln]
        add("S2-02", 2, "依赖全部锁定精确版本", not unlocked,
            f"{len(lines)} 条依赖均带 == 精确版本",
            f"未锁版本 {len(unlocked)} 条：{', '.join(unlocked[:5])}",
            "改为 pkg==x.y.z，执行 pip freeze > requirements.txt 后提交。")
    elif pyproject:
        lock = any((root / n).is_file() for n in ("poetry.lock", "uv.lock", "requirements.lock"))
        add("S2-02", 2, "依赖锁定文件存在", lock,
            "找到 lock 文件", "pyproject.toml 无对应 lock 文件",
            "生成并提交 lock 文件，保证换机可复现。")
    else:
        add("S2-02", 2, "依赖锁定文件存在", False,
            "", "依赖清单缺失，无法校验版本锁定",
            "先补 requirements.txt（见 S2-01），每行带 == 精确版本。")

    env_ex = first_existing(root, [".env.example", "env.example"])
    env_text = read_text(env_ex)
    leaked = re.search(r"sk-[A-Za-z0-9_\-]{20,}", env_text)
    add("S2-03", 2, ".env.example 存在且无真实密钥", env_ex is not None and not leaked,
        f"找到 {env_ex.name}，占位值合规" if env_ex else "",
        "文件缺失" if not env_ex else "疑似包含真实密钥片段（sk- 长串）",
        "复制 assets/env.example；立即轮换疑似泄露的密钥。")

    gi = read_text(first_existing(root, [".gitignore"]))
    need = [".env", "venv", "__pycache__"]
    lack = [k for k in need if k not in gi]
    add("S2-04", 2, ".gitignore 覆盖密钥与缓存目录", gi and not lack,
        "已忽略 .env / venv / __pycache__",
        "缺失：" + (", ".join(lack) if gi else "整个 .gitignore"),
        "复制 assets/gitignore.example 为 .gitignore。")

    tracked = env_is_tracked(root)
    add("S2-05", 2, ".env 未被 git 跟踪", tracked is False,
        "git ls-files 未发现 .env",
        "git ls-files 发现 .env 已被跟踪" if tracked else "无法判定（非 git 仓库或 git 不可用）",
        "执行 git rm --cached .env、确认 .gitignore 生效，并轮换密钥。",
        warn=(tracked is None))

    py_locked = ((root / ".python-version").is_file()
                 or has(r"requires-python", read_text(pyproject))
                 or has(r"FROM\s+python:\d+\.\d+", read_text(first_existing(root, ["Dockerfile"]))))
    add("S2-06", 2, "Python 版本已固定", py_locked,
        "检出 .python-version / requires-python / 固定镜像 tag",
        "未固定 Python 版本",
        "加 .python-version 或 pyproject requires-python；容器镜像不用 latest。", warn=True)


def check_s3(root: Path) -> None:
    code = corpus(root)
    add("S3-01", 3, "FastAPI 入口与端点存在",
        has(r"FastAPI\(", code) and has(r"@app\.(post|get)\(", code),
        "检出 FastAPI( 与路由装饰器", "未检出 FastAPI 应用或路由",
        "复制 assets/api_contract.example.py 作为起点。")

    add("S3-02", 3, "Pydantic 请求/响应模型存在",
        has(r"class\s+\w+\(BaseModel\)", code) and has(r"response_model\s*=", code),
        "检出 BaseModel 子类与 response_model", "缺少 Pydantic 模型或 response_model",
        "所有入参出参用 Pydantic 模型，禁止裸 dict。")

    add("S3-03", 3, "LLM 参数显式设置",
        has(r"timeout\s*=|LLM_TIMEOUT", code) and has(r"temperature", code),
        "检出 timeout 与 temperature", "未检出 timeout / temperature 显式设置",
        "设置 temperature=0.2、timeout=10s、max_tokens 上限。")

    add("S3-04", 3, "检索为空短路",
        has(r"NO_CONTEXT|未找到相关信息|没有找到相关信息", code)
        and has(r"if\s+not\s+\w*(hits|context|docs|results|retrieved)", code),
        "检出空结果短路分支", "未检出检索空短路（幻觉高风险）",
        "Top-K 过滤后为空直接返回，不调用 LLM，见 references/03。")

    add("S3-05", 3, "异常兜底与 error_code",
        has(r"LLM_TIMEOUT|RATE_LIMITED|UPSTREAM_ERROR|PARSE_ERROR", code)
        and has(r"error_code", code),
        "检出分类错误码", "未检出结构化错误码或超时兜底",
        "把超时/限流/解析失败/空输出映射为 error_code + 兜底文案。")

    add("S3-06", 3, "结构化日志含 request_id 与 token_usage",
        has(r"request_id", code) and has(r"token_usage", code),
        "检出 request_id 与 token_usage", "日志字段不全，无法回溯「回答变笨」",
        "按 references/05 补齐结构化 JSON 日志字段。")

    add("S3-07", 3, "Prompt 版本化与引用溯源",
        has(r"PROMPT_VERSION|prompt_version", code) and has(r"sources", code),
        "检出 prompt_version 与 sources", "缺少 Prompt 版本或来源字段",
        "Prompt 独立文件带版本号；sources 需校验来自本次检索。", warn=True)


def check_s4(root: Path) -> None:
    ds = first_existing(root, ["tests/eval_dataset.jsonl", "eval_dataset.jsonl",
                               "tests/eval_dataset_v1.0.jsonl"])
    rows: list[dict] = []
    bad_lines: list[str] = []
    if ds:
        for i, line in enumerate(read_text(ds).splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                bad_lines.append(f"L{i}: {e.msg}")
    add("S4-01", 4, "黄金评测集存在且合法", ds is not None and not bad_lines,
        f"{ds.relative_to(root)} 共 {len(rows)} 条，全部为合法 JSON" if ds else "",
        f"文件缺失或 JSON 非法：{'; '.join(bad_lines[:3]) or '未找到文件'}",
        "复制 assets/eval_dataset.example.jsonl，每行一个 JSON 对象。")

    add("S4-02", 4, "评测集不少于 10 条", len(rows) >= 10,
        f"{len(rows)} 条", f"仅 {len(rows)} 条",
        "至少 10 条，推荐 20-30 条并分层。")

    missing_fields = [r.get("id", "?") for r in rows
                      if not all(k in r for k in ("id", "input", "expected_type"))]
    add("S4-03", 4, "每行含必需字段", not missing_fields,
        "id / input / expected_type 齐全",
        f"缺字段用例：{', '.join(map(str, missing_fields[:5]))}",
        "补齐字段，见 references/04 字段规范。")

    cats = {r.get("category") for r in rows}
    need_cats = {"functional", "boundary", "safety", "robustness"}
    types = {r.get("expected_type") for r in rows}
    add("S4-04", 4, "四层覆盖齐全", need_cats <= cats,
        "functional / boundary / safety / robustness 全覆盖",
        "缺少层级：" + ", ".join(sorted(need_cats - cats)),
        "按 references/04 的分层比例补充用例。")

    add("S4-05", 4, "含 chitchat / refusal / no_context 用例",
        {"chitchat", "refusal", "no_context"} <= types,
        "三类关键意图均有用例",
        "缺少类型：" + ", ".join(sorted({"chitchat", "refusal", "no_context"} - types)),
        "补意图路由回归用例，安全类零容忍。")

    tests = [p for p in py_files(root) if p.name.startswith("test_") and p.name.endswith(".py")]
    add("S4-06", 4, "pytest 测试文件存在", bool(tests),
        f"找到 {len(tests)} 个 test_*.py", "未找到 test_*.py",
        "复制 assets/test_ai_performance.example.py 并重命名。")

    report = read_text(first_existing(root, ["EVAL_REPORT.md", "docs/EVAL_REPORT.md"]))
    has_numbers = has(r"\d+(\.\d+)?\s*%", report) and has(r"p95|延迟|latency", report)
    add("S4-07", 4, "评测报告含实测数值与口径", has_numbers,
        "检出百分比与延迟指标", "EVAL_REPORT.md 缺失或未检出准确率/延迟数值",
        "用 eval_runner.py 生成结果表，写明测试集版本与判定口径。")

    badcase = first_existing(root, ["docs/BAD_CASES.md", "BAD_CASES.md", "docs/badcase_log.md"])
    add("S4-08", 4, "Bad Case 记录存在", badcase is not None,
        f"找到 {badcase.relative_to(root)}" if badcase else "",
        "未找到 Bad Case 记录文件",
        "复制 assets/badcase_log.example.md，逐条归因并复测。", warn=True)


def check_s5(root: Path) -> None:
    readme = read_text(first_existing(root, ["README.md"]))
    start = has(r"uvicorn|docker-compose|python\s+main\.py|pip install -r", readme)
    add("S5-01", 5, "README 含一键启动命令", bool(readme) and start,
        "检出启动命令", "README 缺失或未检出启动命令",
        "写明本地与容器两种启动方式。")

    add("S5-02", 5, "README 含环境变量说明",
        has(r"\.env|环境变量|OPENAI_API_KEY", readme),
        "检出环境变量说明", "未检出环境变量表或 .env 说明",
        "逐项列出变量名、必填、示例值、缺失后果。", warn=True)

    changelog = read_text(first_existing(root, ["CHANGELOG.md"]))
    add("S5-03", 5, "CHANGELOG 含语义化版本号",
        has(r"\[?\d+\.\d+\.\d+\]?", changelog),
        "检出 x.y.z 版本号", "CHANGELOG.md 缺失或无版本号",
        "复制 assets/CHANGELOG_TEMPLATE.md，版本号与 git tag 对应。")

    code = corpus(root)
    add("S5-04", 5, "日志字段齐全（代码侧）",
        has(r"prompt_version", code) and has(r"model", code) and has(r"latency", code),
        "检出 prompt_version / model / latency", "日志字段不全",
        "按 references/05 第 5.2 节补齐，便于回溯。", warn=True)

    deploy = first_existing(root, ["docker-compose.yml", "docker-compose.yaml", "Dockerfile"])
    add("S5-05", 5, "容器化或部署配置存在", deploy is not None,
        f"找到 {deploy.name}" if deploy else "",
        "未找到 Dockerfile / docker-compose.yml",
        "复制 assets/docker-compose.example.yml。", warn=True)


CHECKERS = {1: check_s1, 2: check_s2, 3: check_s3, 4: check_s4, 5: check_s5}


def render(root: Path, stage: int | None, checks: list[Check]) -> str:
    total = len(checks)
    passed = sum(c.status == "pass" for c in checks)
    failed = [c for c in checks if c.status == "fail"]
    warned = [c for c in checks if c.status == "warn"]
    rate = (passed / total * 100) if total else 0.0

    lines = [
        "# AI 全栈交付审计报告",
        "",
        f"- 项目：`{root}`",
        f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 范围：{STAGE_NAMES[stage] if stage else '全部五阶段'}",
        f"- 结果：通过 {passed} 项 / 警告 {len(warned)} 项 / **失败 {len(failed)} 项**"
        f"（严格通过率 {rate:.0f}%）",
        "",
        "> 本脚本只检查形式合规。内容质量（指标口径、原型是否真的三态、Bad Case 归因是否到位）"
        "须按 `references/06-pitfalls-and-gates.md` 人工复核。",
        "",
    ]
    for s in sorted({c.stage for c in checks}):
        group = [c for c in checks if c.stage == s]
        ok = sum(c.status == "pass" for c in group)
        lines += [f"## {STAGE_NAMES[s]}  [{ok}/{len(group)}]", ""]
        for c in group:
            lines.append(f"- {ICON[c.status]} `{c.sid}` {c.title} — {c.detail}")
            if c.fix:
                lines.append(f"    - 修复建议：{c.fix}")
        lines.append("")

    if failed:
        lines += ["## 优先修复（按阶段顺序，前 5 项）", ""]
        for i, c in enumerate(failed[:5], 1):
            lines.append(f"{i}. `{c.sid}` {c.title} → {c.fix}")
        lines.append("")
    lines += ["---", "",
              "交付判定：**无 FAIL 项**才可宣称「已交付」；WARN 项须按 references 人工确认，"
              "未达标项须在 EVAL_REPORT「已知局限性」逐条登记。", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 全栈项目五阶段交付物审计")
    ap.add_argument("--project", default=".", help="项目根目录（默认当前目录）")
    ap.add_argument("--stage", type=int, choices=[1, 2, 3, 4, 5], help="只审计某一阶段")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown")
    ap.add_argument("--out", help="把报告写入文件")
    args = ap.parse_args()

    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f"项目目录不存在：{root}", file=sys.stderr)
        return 2

    stages = [args.stage] if args.stage else [1, 2, 3, 4, 5]
    for s in stages:
        CHECKERS[s](root)

    checks = [c for c in CHECKS if not args.stage or c.stage == args.stage]
    if args.json:
        payload = {
            "project": str(root),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "total": len(checks),
            "passed": sum(c.status == "pass" for c in checks),
            "failed": sum(c.status == "fail" for c in checks),
            "warned": sum(c.status == "warn" for c in checks),
            "checks": [asdict(c) for c in checks],
        }
        output = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        output = render(root, args.stage, checks)

    print(output)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"报告已写入：{args.out}")

    failed = sum(c.status == "fail" for c in checks)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
