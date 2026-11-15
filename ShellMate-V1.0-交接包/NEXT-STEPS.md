# ShellMate · 接下来可以做的操作步骤

> 按阶段排好优先级。**阶段 0 → 1 是必做**：先把「模型从未真跑过」这个洞补上，再谈功能。
> 每步都给了命令、通过标准、失败时的处理。所有命令默认在 `shellmate/` 目录下执行。

---

## 阶段 0 · 在新电脑上确认包是完整的（10 分钟，必做）

| 步骤 | 命令 / 动作 | 通过标准 | 挂了怎么办 |
|---|---|---|---|
| 0.1 解包 | 解压 zip，建议**整个解压目录作为新工作区**打开 | 能看到 `00-START-HERE.md`、`shellmate/`、`.workbuddy/` | 中文目录名乱码时改用 `unzip`/7-Zip 以 UTF-8 解压 |
| 0.2 校验完整性 | `cd ShellMate-V1.0-交接包 && python pack.py --verify` | 输出 `VERIFY OK` + 文件数 | 有 `MISSING`/`CHANGED` 说明拷贝损坏，重新解压 |
| 0.3 Python | `python --version` | 3.10+（开发机 3.13.14） | Linux 上可能是 `python3`，后续命令相应替换 |
| 0.4 闭环 | `cd shellmate && python start.py --mock --no-color --once "怎么查看大于 100M 的文件？"` | 看到解释 + 整行 `find` 命令 + 常量行 | `ModuleNotFoundError` → 确认没有把 `src/` 挪走 |
| 0.5 测试 | `python -m unittest discover -s tests` | **46 tests OK** | 看失败的用例名，多半是路径/平台差异，别直接改断言 |
| 0.6 评测 | `python eval/run_eval.py` | 门控分级准确率 **100.0%** | 出现 `[不一致]` 说明 `gate.py` 或 `cases.jsonl` 被改过 |
| 0.7 视觉对照 | 打开 `design/ShellMate-TUI-交互稿-V1.0.png`，对照 `shellmate/DEMO.md` | 8 屏状态对得上 | — |
| 0.8 让 AI 接手 | 把解压目录设为工作区（Skill 已在 `.workbuddy/skills/shellmate-dev/`）；或复制 `SKILL.md` 到目标工作区同名路径 | 会话里说「按 Skill 继续开发 ShellMate」能读到约束 | Skill 未被识别时，直接把 `HANDOFF.md` 贴进对话 |

> Windows 原生终端能跑 mock，但**真实用途请在 Linux 或 WSL2 的 bash/zsh 里**，生成的命令一律按 bash 语法给出。

---

## 阶段 1 · 接上真实模型，把「未验证」变成数字（最高优先级）

这是当前工程唯一的大洞。目标机器上按顺序做：

**1.1 装 Ollama 并拉模型**

```bash
# 按 Ollama 官网说明安装，然后：
ollama serve                     # 另开一个终端，保持窗口别关
ollama pull qwen3:8b             # Q4_K_M 约 5.2GB，Apache-2.0
ollama list                      # 确认模型在
```

**1.2 先跑一条，确认连通**

```bash
cd shellmate
python start.py --no-color --once "怎么查看当前目录下大于 100M 的文件？"
```

通过标准：不再是 `ollama serve` 指引，而是真的输出一条 `find` 命令。
失败时看 `errors.py` 的话术；WSL 里若 Ollama 装在 Windows 宿主，需 `OLLAMA_HOST=0.0.0.0` + `SHELLMATE_HOST=http://<宿主地址>:11434`。

**1.3 跑真实评测，拿可用率基线**

```bash
python eval/run_eval.py --live                 # 真实模型，逐条 50 个任务
python eval/run_eval.py --live --json > baseline.json   # 存基线，后面调 Prompt 好对比
```

通过标准：**自己定**，但必须诚实记录。原方案期望命令可用率 > 80%；达不到就如实写进结论，不要用 mock 数字冒充。
`--live --mock` 只验链路，不代表模型质量，别拿它交差。

**1.4 量延迟，按硬件分列**

分别在有 GPU / 纯 CPU 的机器上记首字延迟与完整响应时间，同时记录 `num_ctx`（默认 2048）与 KV cache 占用。
纯 CPU 上 7B 通常只有个位数 tok/s，不要套用 TTFT < 1.5s / 完整 < 5s 的单一 SLA。

**1.5 同时确认 Qwen3 的 non-thinking 真的生效**

若响应里冒出思考链或明显变慢，检查 `prompt.py` 传给 `/api/generate` 的 `think: false` 是否被当前 Ollama 版本接受；必要时改用 `/api/chat` 的 `think` 选项，并在 `tests/test_client.py` 补一条断言。

**阶段 1 交付判定**：一份 `baseline.json` + 三个数字（命令可用率 / 疑似幻觉率 / 分硬件延迟），以及「哪些任务失败、为什么」的清单。

---

## 阶段 2 · 按基线调 Prompt 与规则（不做新功能）

**2.1 先分类失败用例**：把 `baseline.json` 里 `failed` 与 `call_errors` 按「解析失败 / 命令不对 / 参数幻觉 / 风险分级错 / 调用超时」归类。

**2.2 对症下药，一次只改一处，每改完重跑 `--live` 对比**

| 症状 | 改哪里 | 配套回归 |
|---|---|---|
| 解析失败多 | `prompt.py` 的行协议分隔符、`client.py` 的容错；7B 对分隔符比 JSON 稳，别急着全切 JSON | `tests/test_client.py` 加该输出样例 |
| 命令不对 / 环境不符 | `prompt.py` 注入的发行版、shell、cwd 描述；`envinfo.py` 的 WSL 判定 | `tests/test_envinfo.py` |
| 幻觉参数 | `prompt.py` 约束 + `gate.py` 的 `KNOWN_OPTIONS`（扩充已知长选项，减少误报） | `eval/cases.jsonl` 补用例 |
| 危险命令漏判 | `gate.py` 加参数级规则；先在 `eval/cases.jsonl` 写期望，再写规则 | `tests/test_gate.py` + 50 条评测仍 100% |
| 超时 / 太慢 | 降 `num_ctx`、缩短 System Prompt、确认 non-thinking | 阶段 1.4 的延迟表 |

**2.3 扩评测集**：把真实用户提问补进 `eval/cases.jsonl`（保持 `id / query / mode / reference_command / expected_risk / expected_pattern` 字段）。每加一条都要先人工确认 `expected_risk`，否则门控准确率会被污染。

**阶段 2 交付判定**：可用率相对 `baseline.json` 有可解释的变化，50+ 条评测仍全绿，46+ 个测试全绿。

---

## 阶段 3 · V1.5 候选功能（按价值排序，一次上一个）

| 优先级 | 功能 | 改动文件 | 验收 |
|---|---|---|---|
| P0 | **报错解释 Error2NL**（设计稿 S07 已画好） | `prompt.py` 加模式、`app.py` 加 `:mode`、`client.py` 解析不变、`render.py` 复用「解释 + 只读排查步骤 + 边界声明」结构 | 不碰日志文件、不自动修复；界面沿用同一套结构，不重做交互 |
| P1 | 本地 Man Pages 轻量检索 | 新增 `src/shellmate/rag.py`（离线、可选依赖）、`prompt.py` 注入命中片段 | 冷门命令准确率提升可测；**不联网**，不引入必须 pip install 的依赖 |
| P2 | 有限多轮上下文 | `config.py` 的 `context_rounds`、`app.py` 的 history 管理 | 仍是「仅保留上一轮」起步，先量对延迟与准确率的影响再放宽 |
| P3 | 100 条真实提问分类统计 | `feedback.py` 增加分类字段与 `:stats` 展示 | 验证原方案的「用户假设」：主要问参数还是报错含义 |

> 每上一个功能，先回 `HANDOFF.md` 第 5 节确认没碰红线，再补测试。**不要顺手加「自动执行」** —— 那会推翻整个产品定位。

---

## 阶段 4 · 工程化（有余力再做）

- 加 CI：GitHub Actions / CNB 上跑 `unittest` + `eval/run_eval.py`（离线部分），把「执行原语扫描」设为必过门禁。
- 发布：`pip install -e .` 已可用；要发 PyPI 再补版本号、CHANGELOG、py.typed 与 wheel 构建。
- 跨平台：macOS 的 `termios` 路径、Windows Terminal 的 OSC 52 支持实测；不支持剪贴板时给更明确的提示。
- 打包更新：改完代码在包根跑 `python pack.py`，会重新生成 `MANIFEST.sha256` 与上级目录的 zip；`python pack.py --verify` 可校验。

---

## 阶段 5 · 真实用户验证（别跳过）

- 找 3–5 位 Linux 初学者，观察他们是否真的在终端里问，而不是切浏览器搜（验证业务假设）。
- 收集前 100 条提问分类（验证用户假设），并把 `y`/`n` 反馈导出来算命令可用率（反馈只在 `~/.local/share/shellmate/feedback.jsonl`，注意取得同意再取用）。
- 重点观察有没有人**盲目复制** `CAUTION` 命令 —— 如果有，说明 S06 的警示不够强，要往交互上改，不是往模型上加。

---

## 明确不要做（V1.0 范围内）

自动执行 / 「回车确认执行」、GUI 或 Web 界面、默认联网或联网搜索、把线上 API 当等价部署选项、把白名单当安全网、把静态规则叫「沙箱」、日志分析与自动调试、用单一 SLA 覆盖 CPU 与 GPU、拿 mock 结果冒充模型指标。

---

## 一页速查

```bash
# 在 shellmate/ 下
python start.py --mock                 # 零安装看全部界面
python start.py                       # 真模型
python -m unittest discover -s tests  # 46 tests
python eval/run_eval.py               # 离线门控自检
python eval/run_eval.py --live        # 真实模型评测（需 Ollama）
python scripts/smoke.py > DEMO.md     # 重生成 8 屏演示
# 在包根下
python pack.py                        # 重新打包
python pack.py --verify               # 校验完整性
```
