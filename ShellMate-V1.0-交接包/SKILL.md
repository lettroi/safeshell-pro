---
name: shellmate-dev
description: ShellMate V1.0（本地命令行助手：轻量 Harness + 7–14B 小模型 + Ollama 本机推理，面向 Linux 初学者）的工程上下文、硬约束与验证门禁。在继续开发、修改、调试、重构、评测、扩展功能或排查该工程时使用；也用于回答「这个工程为什么这样设计 / 接下来做什么」。
---

# ShellMate 开发上下文

## 项目定位

本地 CLI/TUI 助手：自然语言转命令（NL2Cmd）、命令翻白话（Cmd2NL）、命令上屏前做**静态风险提示**。面向 Linux 初学者 / 初级运维。核心卖点是隐私（提问不出机器）、低延迟、低成本；**交互顺手与信任感比模型能力更重要**。

三条铁律（底部常量行任何状态常驻）：
1. 只生成 · 不执行 · 不隔离
2. 命令行先缓冲 → 静态校验 → 整行呈现（解释可流式，命令不流式）
3. 反馈只存本机 · 默认不联网

## 硬约束（改代码前先确认，违反即回退）

- **源码禁止任何执行原语**：`subprocess` / `os.system` / `Popen` / `shell=True` / `eval` / `exec` / `os.popen`。`tests/test_no_execution.py` 静态扫描防回退；环境探测也只读、不起子进程。
- **DENY 必须清空 `CommandResult.command`**（`gate.py: apply_gate()`），不能只靠渲染层隐藏。
- **命令区在过检前绝不上屏**，缓冲区不得被复制/日志/异常带出。这是与「流式输出」的刻意折中：只有解释流式。
- **模型自评的 `risk_level` 一律被确定性规则覆盖**；白名单只标注「未验证命令」，永不拦截。危险来自参数组合，不是命令名。
- **云端默认关闭**，`enable-cloud` 需显式输入 + 二次确认，开启后常驻联网标识；`cloud_enabled` 不接受环境变量开启。独立的 `SHELLMATE_CLOUD_FALLBACK`（默认关闭）仅在本机 Ollama 不可达且配了 `SHELLMATE_CLOUD_ENDPOINT` 时自动回退云端，回退后同样常驻联网标识；它不破坏「默认不联网」。
- **non-thinking 锁定**（Qwen3 思考链会击穿响应目标），上下文仅保留上一轮。
- 界面：底部常量行常驻；风险色一律配文字标签；不用 emoji；命令/路径/状态用等宽字体。
- 禁用话术与入口：「安全沙箱」「请联系管理员」「回车确认执行」。
- 模型未启动给 `ollama serve` 指引，不静默失败；协议解析失败走「重问」，不猜测、不降级成自由文本。
- **不伪造指标**：`--live` 调用失败如实记录，mock 结果不得冒充模型质量。

## 文件地图（`shellmate/`）

| 文件 | 职责 |
|---|---|
| `start.py` | 免安装入口（也可 `pip install -e .`） |
| `src/shellmate/app.py` | 主循环、键位、云开关二次确认、状态编排 |
| `src/shellmate/client.py` | Ollama NDJSON 流式、行协议/JSON 双解析、mock、cloud |
| `src/shellmate/gate.py` | **安全核心**：参数级规则、复合命令拆分、`apply_gate()` |
| `src/shellmate/render.py` | ANSI 渲染、语法分色、常量行、8 屏状态 |
| `src/shellmate/prompt.py` | System Prompt、行协议约定、JSON Schema |
| `src/shellmate/config.py` | 配置与隐私默认值（环境变量可覆盖） |
| `src/shellmate/envinfo.py` | 发行版 / shell / cwd / WSL / GPU 只读探测 |
| `src/shellmate/errors.py` / `feedback.py` | 错误话术 / 本机 jsonl 反馈 |
| `tests/` | 46 个标准库 unittest，含执行原语静态审计 |
| `eval/cases.jsonl` + `run_eval.py` | 50 条评测（SAFE 28 / CAUTION 12 / DENY 10） |
| `scripts/smoke.py` | 无 Ollama 的 8 屏端到端演示 |

**零第三方依赖**：`urllib` + ANSI + `termios`/`msvcrt`，Python 3.10+。不要随手引入 `requests` / LangChain / rich。

## 数据契约

`CommandResult`：`command / risk_level(SAFE|CAUTION|DENY) / reason / explain / params[{token, meaning}]`，另带 `matched_rules / unverified / alternatives / consequences`。

- 默认 `lines` 协议：`EXPLAIN` 段逐字上屏，`---` 之后 `COMMAND/RISK/REASON/PARAMS` **全部缓冲**，解析 + 门控通过才整行呈现。
- 可选 `--format json`：严格 Schema，代价是解释不流式。
- 分级示例：`rm -rf /var` → DENY；`chmod -R 777 ./project` → CAUTION；`find . -type f -size +100M` → SAFE。
- 环境变量：`SHELLMATE_HOST / MODEL / TIMEOUT / FORMAT / DATA_DIR / NO_COLOR / MOCK / CLOUD_ENDPOINT / CLOUD_API_KEY / CLOUD_TOKEN / CLOUD_FALLBACK / CLOUD_MODEL / CLOUD_MODEL_DEEP / CLOUD_MODELS_LIGHT / CLOUD_MODELS_DEEP / CLIENT_ID / NO_POLICY`（`CLOUD_FALLBACK` 为「本地不可达→回退云端」显式开关，默认关闭；`CLOUD_TOKEN` 对应网关可选 `SAFESHELL_API_TOKEN`，网关未开鉴权时留空）。
- **云端候选链 + 团队策略（V1.1，2026-11-15 更新）**：`client.py` 按模式选 5 个启用模型的候选链（`glm-4.7-flash` 已被网关默认停用、默认链移除），停用 / 429 / 5xx / 行协议解析失败都会自动换下一个；`policy.py` 从网关拉团队策略与模型清单（含近 1h 延迟/成功率，慢模型稳定降权），在 `apply_gate()` 之后**只收紧**地叠加禁用正则与风险阈值，DENY 时清空 `command`。拉取失败静默降级，本地模式完全不联网。`eval/cloud_eval.py` 可跑真实模型质量 baseline。

## 常用命令（在 `shellmate/` 下）

```bash
python start.py --mock --no-color --once "怎么查看大于 100M 的文件？"
python start.py --format json
python -m unittest discover -s tests -v     # 期望 46 tests OK
python eval/run_eval.py                      # 期望门控准确率 100.0%
python eval/run_eval.py --live               # 需 Ollama；真实可用率
python scripts/smoke.py > DEMO.md            # 8 屏演示
```

**每次改动后门禁**：`unittest` 全绿 + `eval/run_eval.py` 门控 100% + 执行原语扫描干净。改了 `gate.py` 必须同步 `tests/test_gate.py` 与 `eval/cases.jsonl`。

## 当前状态与首要任务

- 已验证（Windows / Python 3.13.14 / **未装 Ollama**）：46 tests OK；50 条门控 100%；8 屏演示断言成立；JSON 协议、错误话术、设置页、反馈统计、交互主循环均实跑通过。
- **未验证：真实模型推理**。命令可用率、幻觉率、TTFT 必须在装好 `qwen3:8b`（Q4_K_M）的机器上跑 `--live` 才算数。
- **接手第一件事**：阶段 0 解包自检 → 阶段 1 接 Ollama 拿 `baseline.json`，而不是先加功能。详见 `NEXT-STEPS.md`。
- V1.0 不做：自动执行、多轮长上下文、GUI/Web、联网搜索、日志分析/自动调试、RAG。报错解释（设计稿 S07）属 V1.5 预览。

## 设计依据

Ardot 画布 `ShellMate · 本地命令行助手 TUI 交互稿 V1.0`（fileId `746241597406502`，page `0:1`）：8 屏 + 规范条 + 5 步门控链路 INPUT → HARNESS → MODEL → GATE → RENDER，每屏下有交互注记。整图在 `design/`。变量集 `ShellMate Tokens`（16 token）。

## 沟通要求

- 用简体中文；像资深同事一样直接指出方案里的问题，不要盲目照做。
- 明确区分「已实跑验证」与「未验证 / 推测」；不得把 mock 结果说成模型跑通。
- 涉及安全边界的改动，先说明会不会碰上述硬约束，再动手。
