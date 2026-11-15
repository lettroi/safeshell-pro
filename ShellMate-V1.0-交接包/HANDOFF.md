# ShellMate V1.0 · 关键信息交接

> 这份文档回答「这工程为什么长这样、哪些东西不能改、哪些事还没验证」。
> 操作命令看 `NEXT-STEPS.md`，工程用法看 `shellmate/README.md`。

---

## 1. 项目一句话

面向 **Linux 初学者 / 初级运维** 的本地 CLI 助手：用自然语言问出一条命令（NL2Cmd）、把看不懂的命令翻成白话（Cmd2NL），并在命令上屏前做**静态风险提示**。轻量化 Harness + 7–14B 小模型 + 本机推理，卖点是隐私（提问不出机器）、低延迟、低成本。

目标用户最怕三件事：`man` 看不懂、搜索结果太杂、一条 `rm -rf` 毁掉系统。所以交互的核心不是「模型多聪明」，而是**信任感与顺手程度**。

### 三条铁律（任何状态都在底部常驻）

1. 只生成 · 不执行 · 不隔离
2. 命令行先缓冲 → 静态校验 → 整行呈现（解释可流式，命令不流式）
3. 反馈只存本机 · 默认不联网

---

## 2. 对原始 MVP 方案的九处修订（后续都按这个口径）

| # | 原方案 | 修订 | 落点 |
|---|---|---|---|
| 1 | 「调用线上开源模型 API 也行」 | 与隐私/离线卖点直接冲突 → 收成默认关闭、需 `enable-cloud` + 二次确认的体验模式，开启后常驻联网标识 | `config.py` / `app.py` / S08 |
| 2 | WSL「顺便装一下」 | 启动时探测 `/etc/os-release`、`$SHELL`、cwd、WSL2、GPU，注入 System Prompt；比换模型更划算地提准确率 | `envinfo.py` / `prompt.py` |
| 3 | 「安全沙箱检查」 | 名不副实：没有沙箱，只有静态规则 → 正名「静态风险提示」，界面写明不执行、不隔离 | `gate.py` / `render.py` |
| 4 | 白名单当安全网 | 危险来自**参数组合**而非命令名；`rm` 必在白名单里 → 白名单只标「未验证命令」，拦截交给参数级规则 | `gate.py` |
| 5 | DENY 后「请联系管理员」 | 本地工具的用户自己就是管理员 → 改给具体后果 + 可逆替代方案 | `gate.py` 的 `Rule.consequences / alternatives` |
| 6 | 流式输出全部内容 | 逐字打印命令 = 校验完成前危险命令已上屏 → 解释流式、命令区骨架占位、过检后整行呈现 | `client.py` / `app.py` / S02 |
| 7 | 「绝对禁止自动执行」+「回车确认执行」 | 自相矛盾 → V1.0 只保留 `[c]` 复制，界面无任何执行入口 | `app.py` / `render.py` |
| 8 | 单一 TTFT < 1.5s / 完整 < 5s SLA | 纯 CPU 上 7B 只有个位数 tok/s，不可达 → 按 GPU / CPU 分列，注明 `num_ctx` 与 KV cache 影响；KPI 要有采集手段 | `README.md` 第七节 / `eval/` / `feedback.py` |
| 9 | Qwen2.5-7B / Llama3-8B / Mistral-7B | 2026 年该档位主流已是 **Qwen3 8B/14B**（Apache-2.0，Q4_K_M 约 5.2 / 9.3GB）；且必须强制 non-thinking，否则思考链击穿响应目标 | `config.py` / `prompt.py` |

另外砍掉了 LangChain Lite：纯 `urllib` 足够。

---

## 3. 架构与文件地图

```
输入 → [envinfo 环境探测] → [prompt 组 System Prompt]
     → [client 调本机 Ollama /api/generate，NDJSON 流式]
     → [双协议解析 → CommandResult]
     → [gate 参数级确定性二次校验]        ← 安全核心，不交给模型判断
     → [render 解释流式 / 命令缓冲后整行] → 用户手动复制
```

| 文件 | 职责 |
|---|---|
| `start.py` | 免安装入口，也可 `pip install -e .` 装成 `shellmate` |
| `src/shellmate/app.py` | 主循环、键位、云开关二次确认、状态编排 |
| `src/shellmate/client.py` | Ollama 流式、行协议 / JSON 双解析、mock 模型、cloud |
| `src/shellmate/gate.py` | **安全核心**：参数级规则、复合命令拆分、`apply_gate()` |
| `src/shellmate/render.py` | ANSI 渲染、语法分色、常量行、8 屏状态 |
| `src/shellmate/prompt.py` | System Prompt、行协议约定、JSON Schema |
| `src/shellmate/config.py` | 配置与隐私默认值（全部可由环境变量覆盖） |
| `src/shellmate/envinfo.py` | 环境探测（只读，不起子进程） |
| `src/shellmate/errors.py` | 错误话术（含 `ollama serve` 指引） |
| `src/shellmate/feedback.py` | 本机 jsonl 反馈与统计 |
| `tests/` | 46 个标准库 unittest，含执行原语静态审计 |
| `eval/cases.jsonl` | 50 条评测用例（SAFE 28 / CAUTION 12 / DENY 10） |
| `eval/run_eval.py` | 离线门控自检 + `--live` 真实模型评测 |
| `scripts/smoke.py` | 无 Ollama 的 8 屏端到端演示 |

**零第三方依赖**：HTTP 用 `urllib`、彩色用 ANSI、单键读取用 `termios`/`msvcrt`。这是对原方案 `requests` 的一处良性替换 —— 少一步 `pip install`。

---

## 4. 数据契约（改动时最容易踩的地方）

`CommandResult` 字段（两种协议解析后统一）：

```
command / risk_level(SAFE|CAUTION|DENY) / reason / explain / params[{token, meaning}]
+ matched_rules / unverified / alternatives / consequences
```

**默认行协议（`lines`）**：模型输出 `EXPLAIN` / `---` / `COMMAND` / `RISK` / `REASON` / `PARAMS`。
`EXPLAIN` 段逐字上屏；遇到 `---` 后**全部缓冲**，解析 + 门控通过才整行呈现。7B 模型对分隔符比严格 JSON 更稳。
**可选 JSON（`--format json`）**：用 Ollama `format` 传 Schema 严格约束，代价是解释不再流式。

> 这是一处**有意偏离**交接规格：完整 JSON 在解析完成前无法安全地流式展示解释。若有人想「简化成只留 JSON」，等于把铁律 2 推翻，先读 S02 的交互注记。

**门控分级**：`rm -rf /var` → DENY（递归删除 + 系统级路径）；`chmod -R 777 ./project` → CAUTION；`find . -type f -size +100M` → SAFE。
复合命令按 `| && ;` 拆段逐查，跨管道模式（下载直管道执行、fork bomb、覆盖 `/etc`）在拆分前对整条原文再查一遍。
`apply_gate()` 用确定性结论**覆盖**模型自评；DENY 时直接把 `command` 清空，任何渲染路径都泄漏不出去。

**环境变量**：`SHELLMATE_HOST` / `SHELLMATE_MODEL` / `SHELLMATE_TIMEOUT` / `SHELLMATE_FORMAT` / `SHELLMATE_DATA_DIR` / `SHELLMATE_NO_COLOR` / `SHELLMATE_MOCK` / `SHELLMATE_CLOUD_ENDPOINT` / `SHELLMATE_CLOUD_API_KEY` / `SHELLMATE_CLOUD_TOKEN` / `SHELLMATE_CLOUD_FALLBACK` / `SHELLMATE_CLOUD_MODEL` / `SHELLMATE_CLOUD_MODEL_DEEP` / `SHELLMATE_CLOUD_MODELS_LIGHT` / `SHELLMATE_CLOUD_MODELS_DEEP` / `SHELLMATE_CLIENT_ID` / `SHELLMATE_NO_POLICY`。

`SHELLMATE_CLOUD_TOKEN` 对应网关可选的 `SAFESHELL_API_TOKEN`（默认网关不鉴权时留空即可），客户端会在 raw 请求与策略/模型清单拉取时带 `X-API-Token`。
注意 `cloud_enabled` **不接受环境变量开启**，只能交互里显式开启，这是刻意的。`SHELLMATE_CLOUD_FALLBACK` 是独立的「本地 Ollama 不可达 → 回退云端」**显式**开关（默认 `false`）：仅当本机模型不可达且配置了 `SHELLMATE_CLOUD_ENDPOINT` 时才回退，不破坏「默认不联网」的底线；回退触发时界面常驻「● 回退云端 · 数据离开本机」标识。

**SafeShell Pro 云端多模型路由（V1.1，2026-11-15 更新）**：进入云端模式后，客户端按任务类型选候选模型链。默认 **5 个启用模型**：`glm-4-flash` / `Qwen/Qwen2.5-7B-Instruct` / `Qwen/Qwen3-8B` / `THUDM/GLM-4-9B-0414` / `deepseek-ai/DeepSeek-R1-0528-Qwen3-8B`。`glm-4.7-flash` 因智谱免费档长期 429 已由网关默认停用，客户端默认链也移除它（如需恢复需显式设置 `SHELLMATE_CLOUD_MODELS_DEEP` 并在控制台启用）。顺序依据 `eval/cloud_eval.py` 的真实质量/延迟 baseline（见 `eval/baseline_cloud.json`）。NL2Cmd 走深度链、Cmd2NL 走轻量链；任一模型被团队停用、触发 429/5xx、或**输出不合严格行协议**时，自动切到链中下一个并给出 notice，全部失败才报错。状态行显示实际服务的模型名。网关 `/models` 会返回近 1h 实测延迟与成功率，客户端据此把慢/常失败模型**稳定降到链尾**（只降权不新增，绝不放松安全）。

**云端质量评测**：`python eval/cloud_eval.py --limit 8 --workers 3` 逐模型打真实网关，产出可用率 / 行协议合规率 / p50·p95 / 失败原因到 `eval/baseline_cloud.json`；会消耗免费额度，样本小仅供排序参考。

**团队策略本地强制执行（V1.1）**：云端模式下由 `src/shellmate/policy.py` 从 `cloud_endpoint` 推导拉取 `GET /api/v1/policy` 与 `GET /api/v1/models`（短超时、失败静默降级为「无额外策略」，绝不阻断主链路）。策略只做**收紧**：① 停用的模型从候选链移除；② `allowed_models` 白名单过滤；③ `max_tokens` 注入请求；④ `apply_gate()` 之后叠加 `deny_patterns` 正则与 `risk_threshold`，命中即 DENY 并清空 `command`（沿用红线）。离线/本地推理模式完全不触发任何策略网络请求。`SHELLMATE_NO_POLICY=1` 可关闭拉取。

---

## 5. 不变量 / 红线（继续开发时别改坏）

1. **源码里不许出现任何执行原语**：`subprocess` / `os.system` / `Popen` / `shell=True` / `eval` / `exec` / `os.popen`。`tests/test_no_execution.py` 会静态扫描防回退，GPU 探测也只做只读判断。
2. **DENY 必须清空 command**，不能只靠渲染层隐藏。
3. **命令区在过检前不上屏**，缓冲区内容不得被复制、日志或异常信息带出。
4. **底部常量行任何状态常驻**；风险色一律配文字标签（照顾色弱）；不用 emoji（终端字体渲染不可靠）。
5. **云端默认关闭**：显式 `enable-cloud` + 二次确认才开启，开启后常驻联网标识。**例外**：`SHELLMATE_CLOUD_FALLBACK` 是单独的显式开关（默认关闭），仅在本机 Ollama 不可达且已配置 `SHELLMATE_CLOUD_ENDPOINT` 时自动回退，回退后同样常驻联网标识；它不改变「默认不联网」，只是给「本地挂了」一个可控的逃生通道。
6. **non-thinking 锁定**、上下文仅保留上一轮；V1.0 不做 RAG / 长记忆。
7. **白名单只标注「未验证」，永不承担拦截**。
8. **禁用「安全沙箱」「请联系管理员」「回车执行」** 这类话术与入口。
9. 模型未启动时**不静默失败**，给 `ollama serve` 指引；schema/协议解析失败走「重问」，不猜测、不降级成自由文本。
10. 任何指标不得伪造：`--live` 调用失败要如实记录。

---

## 6. 验证状态（原开发机：Windows / Python 3.13.14 / **未装 Ollama**）

| 检查 | 命令 | 结果 |
|---|---|---|
| 单元测试 | `python -m unittest discover -s tests -v` | **46 tests OK** |
| 离线门控评测 | `python eval/run_eval.py` | 50 条分级准确率 **100%**（SAFE 28 / CAUTION 12 / DENY 10），参考命令疑似幻觉率 0% |
| 8 屏端到端 | `python scripts/smoke.py > DEMO.md` | DENY 不输出命令 / CAUTION 保留命令 / SAFE 放行，三条断言 True |
| 分支实跑 | `--format json`、无 Ollama 错误话术、`:settings`、`:stats`、交互主循环与 `:mode` 切换 | 全部通过 |
| 语法与安全扫描 | `python -m compileall -q ...` + 执行原语 grep | 通过，干净 |

开发中修掉过的真问题（回归用例已留）：`strip("`")` 曾先剥掉 Markdown 代码块标记从而绕过校验；`sudo`/`env` 前缀让删除规则漏判；fork bomb 被管道拆分后漏检；`=>` 被误判成重定向；测试单独 `discover` 时源码路径注入失效。

### 未验证项（必须如实转达）

- **真实模型推理没跑过** —— 原机无 Ollama。命令可用率、幻觉率、TTFT 都不是纸面数字，须在目标机器 `--live`。
- 静态规则不可能穷尽所有危险组合；`CAUTION` 只是提示，不替用户判断。
- 剪贴板依赖终端支持 OSC 52；不支持时手动选中复制。
- `Cmd2NL` 对复合命令的解释依赖 7B 能力，可能出错；安全结论仍由规则引擎给出。

### V1.0 明确不做

自动执行、多轮长上下文、GUI/Web、联网搜索、日志分析 / 自动调试、RAG。报错解释（设计稿 S07）标为 **V1.5 预览**，不进 V1.0 验收。

---

## 7. 设计资产

- 画布：**ShellMate · 本地命令行助手 TUI 交互稿 V1.0**，Ardot fileId `746241597406502`，page `0:1`。
  结构：标题区 + 三条铁律 / 3 张规范卡（色彩语义、字阶字体、状态规则）/ 8 屏（S01 空闲、S02 缓冲态、S03 NL2Cmd 结果、S04 Cmd2NL、S05 高危拒绝、S06 中风险、S07 报错解释 V1.5、S08 设置与隐私）/ 5 步门控链路 INPUT → HARNESS → MODEL → GATE → RENDER。每屏下有交互注记。
- 变量集 `ShellMate Tokens`（16 个 token）：`#0A0C0E` 底、`#9EFF00` 品牌、SAFE `#3DDC97` / INFO `#58A6FF` / CAUTION `#F5A524` / DANGER `#FF5C5C`。
- 整图：`design/ShellMate-TUI-交互稿-V1.0.png`（本包内）。
- 约定：中文 Noto Sans SC；命令、参数、路径、状态一律 JetBrains Mono ≥ 13px；键盘优先；反馈用 `y`/`n` 键位。

---

## 8. 一句话交接

Harness 已经完整、可跑、可测、边界清晰；**唯一没验的是模型本身**。下一台电脑的第一优先级是接上 `qwen3:8b` 拿到真实数字，再按数字调 Prompt 与规则，而不是先加功能。
