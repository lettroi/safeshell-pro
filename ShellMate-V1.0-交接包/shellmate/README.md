# ShellMate V1.0 · 本地命令行助手

用自然语言问出一条 Linux 命令、把看不懂的命令翻译成白话、并在命令上屏前做**静态风险提示**。
面向 Linux 初学者和初级运维；模型跑在本机，提问不出机器。

> **三条铁律**
> ① 只生成 · 不执行 · 不隔离（底部状态栏任何状态常驻）
> ② 命令行先缓冲 → 静态校验 → 整行呈现（解释可流式，命令不流式）
> ③ 反馈只存本机 · 默认不联网

---

## 一、三步跑起来

**本机实测环境**（2026-11-11）：Windows / Python 3.13.14 / **未安装 Ollama**。
所以下面第 1 步在这台机器上已经验证过；第 2、3 步需要在你自己装好 Ollama 的机器（Linux 或 WSL2）上完成。

### 第 1 步：零安装先看效果

```bash
cd shellmate
python start.py --mock
```

`--mock` 用内置确定性样例代替模型，不联网、不需要 Ollama，能完整跑通所有界面状态。
想一次跑完 8 屏并存成文件：

```bash
python scripts/smoke.py > DEMO.md
```

### 第 2 步：装本地模型

按 Ollama 官网说明安装后：

```bash
ollama serve            # 另开一个终端，保持窗口别关
ollama pull qwen3:8b    # Q4_K_M，约 5.2GB，Apache-2.0
```

### 第 3 步：正常运行

```bash
python start.py
```

可选：装成全局命令 `python -m pip install -e .`，之后直接 `shellmate`。

**WSL2 说明**：建议在 WSL2 的 bash/zsh 里运行，生成的命令一律按 bash 语法给出。
若 Ollama 装在 Windows 宿主上，需让服务监听 `0.0.0.0`（设置 `OLLAMA_HOST=0.0.0.0`），
再在 WSL 里指定 `SHELLMATE_HOST=http://<宿主地址>:11434 python start.py`。
Windows 原生终端会给出提示，但不会按 PowerShell 语法生成命令。

---

## 二、怎么用

启动后直接输入问题，回车。结果出来后按单个键：

| 键 | 作用 |
|---|---|
| `c` | 复制命令到剪贴板（OSC 52，不支持的终端请手动选中） |
| `y` / `n` | 有用 / 没用，写进本机反馈文件 |
| `s` | 看更安全的写法（中风险时） |
| `Tab` | 展开参数逐段拆解 |
| `q` / `Esc` | 退出 |

冒号命令：`:mode` 切换 NL2Cmd / Cmd2NL、`:settings` 设置与隐私、`:stats` 本机反馈统计、`:q` 退出。

常用参数：

```bash
python start.py --mock --no-color --once "怎么查看大于 100M 的文件？"
python start.py --format json          # 严格 JSON schema（解释不再流式）
python start.py --settings
python start.py --stats
```

---

## 三、工程结构

```
shellmate/
├── start.py                    免安装入口
├── pyproject.toml              打包配置（dependencies = []）
├── README.md
├── src/shellmate/
│   ├── app.py                  主循环、键位、云开关二次确认
│   ├── client.py               Ollama 流式、双协议解析、mock、cloud
│   ├── config.py               配置与隐私默认值
│   ├── envinfo.py              发行版 / shell / cwd / WSL / GPU 探测（只读）
│   ├── errors.py               错误话术（含 ollama serve 指引）
│   ├── feedback.py             本机 jsonl 反馈与统计
│   ├── gate.py                 参数级静态风险规则引擎（安全核心）
│   ├── prompt.py               System Prompt、行协议、JSON schema
│   └── render.py               ANSI 渲染、语法分色、常量行
├── tests/                      标准库 unittest
├── eval/cases.jsonl            50 条评测用例
├── eval/run_eval.py            评测脚本
└── scripts/smoke.py            无 Ollama 端到端演示
```

**零第三方依赖**：HTTP 用 `urllib`、流式按 NDJSON 逐行读、彩色用 ANSI、单键读取用 `termios`/`msvcrt`，全部标准库。
这是对原方案里 `requests` 的一处良性替换——少一步 `pip install`，更贴「部署有多简单」。

---

## 四、安全设计（这里没有沙箱）

名为**静态风险提示**，不是「安全沙箱」。它只检查命令文本，不执行、不隔离、不验证命令是否真的能跑。
界面每一步都写明边界，避免虚假安全感。

- **参数级规则**，不看命令名：`rm` 在白名单里，但 `rm -rf /var` 命中「递归强制删除 + 系统级路径」→ `DENY`；
  `chmod -R 777 ./project` 命中「递归 + 777」→ `CAUTION`；`find . -type f -size +100M` → `SAFE`。
- 复合命令按 `|` / `&&` / `;` 拆段逐段检查，跨管道模式（下载直管道执行、fork bomb、覆盖系统文件）在拆分前对整条原文再查一遍。
- `DENY` 时 `apply_gate()` 直接清空命令字段，**任何渲染路径都不可能把它显示出来或复制出去**，改为给具体后果 + 可逆替代（如 `du -sh /var/* | sort -h`）。
- 白名单只用于标注「未验证命令」，**不承担拦截**。
- 模型自评的 `RISK` 只是参考，最终等级由 `gate.py` 覆盖。
- 源码里有静态审计测试（`tests/test_no_execution.py`）扫描 `subprocess` / `os.system` / `shell=True` / `eval` / `exec` 等执行原语，防止后续改动把执行能力加回来。

---

## 五、输出协议：一处有意偏离

交接规格要求 JSON 结构化输出。但完整 JSON 在解析完成前无法安全地流式展示解释——
逐字打印命令等于校验完成前危险命令已经上屏。因此做了双协议：

- **默认 `lines`**：模型输出 `EXPLAIN` / `---` / `COMMAND` / `RISK` / `REASON` / `PARAMS`。
  `EXPLAIN` 段逐字上屏；遇到 `---` 后全部缓冲，解析 + 门控通过才整行呈现。
  7B 模型对分隔符也比严格 JSON 更稳。
- **可选 `json`**：`--format json`，用 Ollama 的 `format` 传 JSON Schema 严格约束，代价是解释不流式。

两种协议解析后都产出同一个 `CommandResult`：`command / risk_level / reason / explain / params`，字段与规格一致。
Qwen3 的 thinking 模式被强制关闭（`think: false`），避免思考链击穿响应时间目标。

---

## 六、隐私与线上模式

- 默认走 `http://127.0.0.1:11434` 的本机 Ollama，不联网。
- 线上开源模型 API **只作为体验模式**：默认关闭，需在输入框敲 `enable-cloud` 并二次确认；
  开启后界面常驻「线上 · 数据会离开本机」，`disable-cloud` 随时切回。
- 反馈写 `~/.local/share/shellmate/feedback.jsonl`（可用 `SHELLMATE_DATA_DIR` 改路径），只写本机、不上传，界面明示位置。
- 环境变量：`SHELLMATE_HOST` / `SHELLMATE_MODEL` / `SHELLMATE_TIMEOUT` / `SHELLMATE_FORMAT` / `SHELLMATE_DATA_DIR` / `SHELLMATE_NO_COLOR` / `SHELLMATE_MOCK` / `SHELLMATE_CLOUD_ENDPOINT` / `SHELLMATE_CLOUD_API_KEY` / `SHELLMATE_CLOUD_TOKEN` / `SHELLMATE_CLOUD_FALLBACK` / `SHELLMATE_CLOUD_MODELS_LIGHT` / `SHELLMATE_CLOUD_MODELS_DEEP` / `SHELLMATE_CLIENT_ID` / `SHELLMATE_NO_POLICY`。
- 云端默认启用 5 个免费模型（`glm-4.7-flash` 因智谱长期 429 已默认停用）；候选链顺序依据 `eval/cloud_eval.py` 实测，详见 `eval/baseline_cloud.json`。

### 云端兜底（需显式开启）

本机没有 Ollama 时，可由独立开关 `SHELLMATE_CLOUD_FALLBACK=true` 回退到 SafeShell Pro 网关（数据会离开本机，界面常驻标识）：

```bash
SHELLMATE_CLOUD_FALLBACK=true \
SHELLMATE_CLOUD_ENDPOINT="http://127.0.0.1:8772/api/v1/raw/completions" \
python start.py --once "怎么查看大于100M的文件"
```

云端按任务在 6 个免费模型间自动路由；模型被停用、过载或输出不合行协议时自动换下一个。团队管理员在网关「团队控制台 → 免费模型清单」停用某模型后，客户端下次拉取清单会自动跳过它（政策变化不再免费时无需改代码）。网关下发的禁用正则与风险阈值会在本地 `gate.py` 之后叠加执行，DENY 同样清空命令。详见 `HANDOFF.md`。

---

## 七、指标与验收

```bash
python -m unittest discover -s tests -v     # 单元与静态审计
python eval/run_eval.py                      # 50 条离线链路自检
python eval/run_eval.py --live               # 装好 Ollama 后算命令可用率
```

`cases.jsonl` 覆盖 50 条常见任务（SAFE 28 / CAUTION 12 / DENY 10）。离线自检不依赖模型，
验的是 harness 本身：门控分级准确率、参考命令疑似幻觉率。
命令可用率与幻觉率需要真实模型逐条生成，必须 `--live`；本机没有 Ollama 时会如实记为调用失败，**不伪造通过率**。

延迟指标按硬件分列，不写单一 SLA：

| 场景 | 说明 |
|---|---|
| 8GB 显存 / 16GB 统一内存 | 7–8B Q4_K_M 约 5–7GB，TTFT < 1.5s / 完整 < 5s 才可能达成 |
| 纯 CPU | 7B 通常只有个位数 tok/s，上述目标基本不可达；应延长超时或换更小模型 |

`num_ctx`（默认 2048）与 KV cache 会明显影响首字延迟。

---

## 八、已知限制（V1.0）

- **真实模型推理在本机未验证**：没有 Ollama，所有已验证结果来自 mock 与离线自检。命令可用率、幻觉率、TTFT 需在目标机器重跑 `--live`。
- 静态规则不可能穷尽所有危险组合；`CAUTION` 只是提示，不替用户判断。
- 剪贴板依赖终端支持 OSC 52；不支持时请手动选中复制。
- `Cmd2NL` 对复合命令的解释依赖模型能力，7B 可能出错；安全结论仍由规则引擎给出。
- **V1.0 明确不做**：自动执行、多轮长上下文（仅保留上一轮）、GUI/Web、联网搜索、日志分析/自动调试、RAG。
  报错解释（S07）属 V1.5 预览，不进 V1.0 验收。

设计依据：`ShellMate · 本地命令行助手 TUI 交互稿 V1.0`（8 屏 + 规范条 + 5 步门控链路）与
`ShellMate-TUI-交互稿-V1.0.png`。本工程按该交互稿的逐屏注记实现。
