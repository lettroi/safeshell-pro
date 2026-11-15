# SafeShell Pro 用户手册

> 版本：2026-11-15（含免费化、改进路线、dockerd 网络修复与 WSL 迁移）｜ **主工作区：`/root/safeshell-pro`（WSL2 ext4）**，Windows 侧 `C:\Users\...\软件开发` 已转为只读归档
> 适用：网关 + 控制台 `release/mc_src`，客户端 `ShellMate-V1.0-交接包/shellmate`
> 一句话：把自然语言变成**经过安全门控**的运维命令，推理走免费大模型（实付 0 元）、全程可审计。
> **完全免费 · AGPL-3.0 全量开源**：软件 0 元、无付费档、无需注册、无官方托管与 SLA。全部功能（含团队控制台、审计、策略、限流）一次给全；唯一需要自备的是各厂商免费 API Key。
> 配套阅读：[`SafeShellPro-评估与改进路线.md`](SafeShellPro-评估与改进路线.md)（性能、选型、Docker 理由、改进路线）、[`SafeShellPro-商业项目方案.md`](SafeShellPro-商业项目方案.md)（技术方案与 2026-11-15 定位变更）、[`release/mc_src/LICENSE`](release/mc_src/LICENSE)（AGPL-3.0 全文）。

---

## 0. 先记住三件事

| | 说明 |
|---|---|
| **只生成 · 不执行 · 不隔离** | 工具只把命令**写给你看**，绝不替你运行，也没有沙箱。复制后请自己粘贴执行。 |
| **本地优先 · 云端兜底** | 本机装了 Ollama 就完全离线；没装才（需显式开启）走云端，界面常驻「● 回退云端 · 数据离开本机」。 |
| **免费 ≠ 没成本口径** | 控制台「预计节省」是**对照估算价，不是账单**；免费模型实付恒为 ¥0。 |

默认端口 **8772**（`http://127.0.0.1:8772`）。**不要使用 8000**，本机端口台账中它是禁用端口。

---

## 1. 一分钟装好（推荐：Docker 一键）

### 1.1 前置条件

| 平台 | 需要 |
|---|---|
| Windows 10/11 | 已装 **WSL2** + WSL 内的 Docker（Docker 29.x 已实测）；或 Docker Desktop |
| Linux / macOS | Docker Engine + `docker compose` v2 |
| 仅跑客户端 | Python 3.10+（**零第三方依赖**），不需要 Docker |

首次安装**不需要任何 API Key**：装完即可用演示模式体验全部界面；想让模型真正回答，再按 §2 填自己的免费 Key。

### 1.2 一键安装

**主入口是 WSL（代码已迁到 ext4）**：

```bash
cd /root/safeshell-pro/release/mc_src
bash install.sh
```

脚本自动：检查 Docker → 生成 `.env`（仅占位符、无密钥）→ 检查 bridge NAT → 构建镜像 → 启动容器 → 等健康检查 → 打印地址。首次约 1–3 分钟（需下载 Node/Python 基础镜像与依赖）。

**Windows 侧**：`install.bat` 仍可双击，但它只是转到 WSL 的**转发器（legacy）**；`keep-wsl.bat` 用于保持常驻窗口。Windows 侧原工作区已只读归档，请勿再从那里启动服务（会与 WSL 主工作区抢 8772）。

**命令行子命令**（`bash install.sh <子命令>`）：

| 子命令 | 作用 |
|---|---|
| `install`（默认） | 构建 + 启动 + 健康检查 |
| `status` | 查看容器状态与健康检查 |
| `logs` | 跟随日志（`Ctrl-C` 退出查看，不停服务） |
| `stop` | 停止并移除容器，**保留 `data/` 与 `logs/`** |
| `restart` | 重启服务 |
| `doctor` | 诊断 Docker / WSL / 端口 / 网络模式 / bridge NAT / 凭据文件 |
| `fix-network` | 修复 WSL dockerd 的 bridge NAT（幂等；见 §5） |
| `keepalive` | **常驻 WSL 会话**：每 30s 守护 dockerd + 容器 + health（见下） |
| `keepalive-once` | 只做一轮检查与自愈后退出 |
| `keepalive-stop` | 通知常驻会话退出 |

#### WSL 用户必读：保持一个终端窗口常驻

WSL2 在**最后一个会话退出后会休眠实例**。实例一休眠，容器就跟着重启，Windows 侧会间歇出现 `502`。解决办法：

1. **双击 `release/mc_src/keep-wsl.bat`** —— 会打开一个标题为 `SafeShell Pro - WSL keepalive` 的窗口，**最小化即可，不要关闭**；
2. 需要开机自动常驻：`Win+R` → 输入 `shell:startup` → 把 `keep-wsl.bat` 的快捷方式放进去；
3. 也可在 WSL 终端里手动执行 `bash keepalive.sh`（或 `bash install.sh keepalive`）。

常驻脚本每 30 秒检查一次：dockerd 不可达就拉起服务、容器不在就 `docker compose up -d`、health 不通会提示。结束用 `bash keepalive.sh --stop`。若换用 **Docker Desktop**（bridge 模式），则无需常驻窗口。

### 1.3 确认装好了

```bash
curl http://127.0.0.1:8772/api/v1/health
# {"status":"ok", ...,"db_ok":true,"poller_alive":true}
```

浏览器打开 `http://127.0.0.1:8772` → 应看到 **SafeShell Pro** 界面。左侧导航共 6 个视图。

### 1.4 另外两种安装方式（按需）

**B. 本地 Python（开发 / 无 Docker）**

```bash
cd release/mc_src
python -m venv venv
./venv/Scripts/pip install -r requirements.txt     # Windows
# 或 venv/bin/pip install -r requirements.txt     # Linux/macOS
cp .env.example .env
./venv/Scripts/python -m uvicorn server.main:app --host 127.0.0.1 --port 8772
```

前端 `web/dist` 已预构建，不装 Node 也能看到完整页面。改前端才需要 `cd web && npm install && npm run dev`（开发端口 21080，代理指向 8772）。

**C. 免安装运行包（历史归档）**

`release/margin-console-v0.2.0-免安装运行包.zip` 是**合并前的 v0.2.0 交付物**：双击 `start.bat`、端口 8000、只含额度路由网关，没有 SafeShell Pro 的 6 模型路由、审计、策略、团队控制台。仅用于归档复核，新部署请用方式 A/B。

---

## 2. 首次接入免费大模型（约 3 分钟）

装完是**演示模式**（额度、趋势、对话回答均为本地模拟，不联网、不花钱）。要真实推理，走「新手接入向导」四步，或直接看已经配好的 6 个模型。

### 2.1 支持的免费模型

当前**默认启用 5 个**（`glm-4.7-flash` 已于 2026-11-15 默认停用，见下）：

| 平台 | 模型 | 适合 / 状态 |
|---|---|---|
| 智谱 BigModel | `glm-4-flash` | 快、30 并发；**当前默认首选**（解释 / 摘要 / 命令生成） |
| 硅基流动 | `Qwen/Qwen2.5-7B-Instruct` | 快，行协议偶有不合规，客户端会自动换模型 |
| 硅基流动 | `Qwen/Qwen3-8B` | 通用、行协议遵循好；冷启动可能 8–20s |
| 硅基流动 | `THUDM/GLM-4-9B-0414` | 编程强（替代已失效的 `glm-4-9b-chat`）；冷启动 38–44s |
| 硅基流动 | `deepseek-ai/DeepSeek-R1-0528-Qwen3-8B` | 推理型，慢且啰嗦，仅作兜底 |
| 智谱 BigModel | `glm-4.7-flash` | 200K 上下文、编程强 —— **默认停用**：免费档长期 429（错误码 1305）且常挂起至超时，实测拖慢全链路到 66s。可在团队控制台「启用」恢复，但不建议 |

候选链顺序会依据 `/models` 返回的近 1h 实测延迟与成功率自动降权慢模型；网关对超时/429 的模型还有 5 分钟自动熔断冷却。

同一平台的多个模型共用一把 Key。Key 只存在本机 `data/margin.db`（已被 gitignore），页面与日志只显示脱敏值。

### 2.2 向导四步

1. **看模式**：读懂「演示 vs 接入后」哪些数据是真的。
2. **选平台**：从预设清单选（国内推荐智谱 / 硅基流动），页面直达官方注册页、Key 页、免费政策页。
3. **领 Key 并测试**：在官方控制台创建 API Key → 粘贴 → 点「测试连接」（真发一条极小请求验证 Key / 端点 / 模型名）→ 通过后「保存并启用」。
4. **发一条消息**：看到真实模型名、延迟与 token 即接好；顶栏 `DEMO` 变 `LIVE`。

> 本工具**不代注册、不生成密钥、不上传密钥**。免费政策变化很快，注册前请点官方链接复核；「一次性赠金额度」不等于长期免费层，建议开启厂商「用完即停」。

### 2.3 迁移已有配置

换机器时把旧的 `data/margin.db` 放到新机器的 `data/` 目录即可（含凭据，请妥善保管、勿上传公开仓库）。或重新走一遍向导。

---

## 3. 日常使用

### 3.1 Web 控制台（6 个视图）

| 视图 | 用途 |
|---|---|
| **产品与定价** | 产品介绍与「完全免费 · 没有付费版」说明（已无价格梯度） |
| **新手接入向导** | 四步接入真实模型；可随时移除配置回到演示模式 |
| **额度总览** | 数据源额度、策略、KPI、24h 趋势、告警事件（**额度/趋势仍是演示数据**） |
| **调用控制台** | 请求配置、代码示例、真实响应、结构化日志、注入 429 演练 |
| **对话测试台** | 多会话、来源引用、SSE 六阶段真实加载态、赞踩反馈 |
| **团队控制台** | 用量 / 审计 / 安全策略 / 免费模型清单 / 完整性校验 —— 全部免费开放 |

**团队控制台细项**

- **审计完整性 / 导出 / 访问令牌**：点「校验哈希链」验证审计是否被篡改（只存命令 SHA-256，不存原文）；可导出 CSV / JSONL 归档；仅当服务端开启了 `SAFESHELL_API_TOKEN` 时，在这里填一次访问令牌（保存在浏览器本地，不上传）。
- **成本与用量**：总调用、累计 Token、平均延迟、实付成本（恒 ¥0）、预计节省（对照估算价，**非账单**）；按模型/模式拆解。
- **安全门控策略**：禁用命令正则、最高放行风险等级、模型白名单、单次最大 token、强制本地门控。保存后客户端下次拉取生效，只收紧不放宽。
- **免费模型清单**：显示每个模型的接入状态、启用状态、健康度（熔断中剩余分钟）。点「停用」并填写原因即完成政策下线；点「启用」恢复。
- **审计明细**：每次调用的时间、客户端、模型、模式、Token、延迟、成败、节省额。

### 3.2 两种「模型不可用」的处理（勿混淆）

| 情况 | 谁处理 | 表现 |
|---|---|---|
| **政策变化，模型不再免费** | 管理员**人工停用** | 网关 `403 / MODEL_DISABLED`；客户端拉清单后从候选链移除；控制台显示「已停用」 |
| **临时过载 / 超时 / 429** | 网关**自动熔断 5 分钟** | 网关毫秒级 `429 / MODEL_COOLDOWN`；客户端立即切下一个候选；控制台显示「熔断中」；到期自动恢复 |

### 3.3 ShellMate CLI（命令行助手）

零第三方依赖，Python 3.10+ 直接跑：

```bash
cd ShellMate-V1.0-交接包/shellmate
python start.py --mock                    # 零配置看效果（内置样例，不联网）
python start.py                           # 本机 Ollama（需 ollama serve + pull qwen3:8b）
```

**连接网关（云端兜底）**：

```bash
# Windows (Git Bash / PowerShell 语法按各自环境变量写法调整)
SHELLMATE_CLOUD_FALLBACK=true \
SHELLMATE_CLOUD_ENDPOINT="http://127.0.0.1:8772/api/v1/raw/completions" \
python start.py --once "怎么查看大于100M的文件"
```

Windows CMD 写法：

```bat
set SHELLMATE_CLOUD_FALLBACK=true
set SHELLMATE_CLOUD_ENDPOINT=http://127.0.0.1:8772/api/v1/raw/completions
python start.py --once "怎么查看大于100M的文件"
```

常用参数与键位：

| 项 | 说明 |
|---|---|
| `--once "问题"` | 跑一次即退出（脚本化/验证用） |
| `--mode nl2cmd\|cmd2nl` | 自然语言转命令 / 命令翻白话 |
| `--mock` | 内置样例模型 |
| `--format json` | 严格 JSON 协议（解释不再流式） |
| `--settings` / `--stats` | 设置与隐私页 / 本机反馈统计 |
| 交互键 `c` `y` `n` `s` `Tab` `q` | 复制 / 有用 / 没用 / 更安全写法 / 参数拆解 / 退出 |
| 冒号命令 `:mode` `:settings` `:stats` `:q` | 模式切换 / 设置 / 统计 / 退出 |

**环境变量速查**

| 变量 | 默认 | 说明 |
|---|---|---|
| `SHELLMATE_CLOUD_FALLBACK` | `false` | 本地 Ollama 不可达时回退云端（**显式开关**） |
| `SHELLMATE_CLOUD_ENDPOINT` | 空 | 网关 raw 端点 |
| `SHELLMATE_CLOUD_MODELS_DEEP` | 5 模型候选链 | NL2Cmd 深度链，逗号分隔 |
| `SHELLMATE_CLOUD_MODELS_LIGHT` | 3 模型候选链 | Cmd2NL 轻量链 |
| `SHELLMATE_CLOUD_MODEL` / `_DEEP` | `glm-4-flash` / `glm-4.7-flash` | 单模型覆盖（插到候选链最前） |
| `SHELLMATE_CLIENT_ID` | `shellmate-cli` | 网关限流与审计的客户端归属 |
| `SHELLMATE_NO_POLICY` | 未设 | 设为 `1` 关闭策略拉取 |
| `SHELLMATE_TIMEOUT` | `60` | 请求超时秒（慢模型建议 90） |
| `SHELLMATE_NO_COLOR` / `SHELLMATE_MOCK` | 未设 | 关闭彩色 / 强制 mock |

### 3.4 三个典型场景

**A. 生成一条安全命令**：CLI 提问 → 回退云端 → 候选链挑模型 → 生成 → 本地 `gate.py` 参数级校验 → 团队策略叠加 → 整行呈现。高危命令 DENY 时**命令文本不输出、也不进剪贴板**。

**B. 某模型不再免费**：团队控制台 → 免费模型清单 → 该行点「停用」→ 填原因。此后网关硬拒绝、客户端自动跳过；恢复免费后点「启用」。**无需改代码或重启**。

**C. 下发一条禁令**：团队控制台 → 策略编辑器加一条正则（如 `rm\s+-rf`）→ 保存。客户端下次调用拉取到策略，在 `apply_gate()` 之后叠加执行，命中即 DENY 并清空命令。

---

## 4. 运维手册

### 4.1 数据在哪

| 路径 | 内容 |
|---|---|
| `release/mc_src/data/margin.db` | SQLite：凭据、审计明细、策略、模型启停、熔断状态、会话与日志 |
| `release/mc_src/logs/` | 结构化 JSON 日志 |
| `~/.local/share/shellmate/feedback.jsonl` | CLI 本机反馈（可用 `SHELLMATE_DATA_DIR` 改） |

容器通过 `./data`、`./logs` 卷挂载，**停止容器不会丢数据**。

### 4.2 备份 / 恢复 / 升级 / 卸载

```bash
# 备份（先停服务更稳妥）
bash install.sh stop
cp data/margin.db data/margin.db.bak.$(date +%Y%m%d)

# 恢复
cp data/margin.db.bak.20261115 data/margin.db
bash install.sh

# 升级代码后重建镜像
bash install.sh stop
bash install.sh            # 会自动 build + up

# 完全卸载（保留数据）：
bash install.sh stop
# 彻底清理（含数据，谨慎）：
#   docker rmi mc_src-app:latest
#   rm -rf data/ logs/
```

### 4.3 改端口

`APP_PORT` 默认是 8772，改 `.env` 的 `APP_PORT` 并同步 `docker-compose.yml` 的 `ports` / healthcheck，以及前端 `web/vite.config.ts` 代理、CLI 的 `SHELLMATE_CLOUD_ENDPOINT`。CLI 端点也要跟着改。**改动前先查 `ai-fullstack-delivery/PORTS.md` 并实时探测端口**，避免与其他作业冲突。

### 4.4 关键配置（`.env`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_PORT` | `8772` | 服务端口 |
| `SFP_NETWORK_MODE` | `bridge` | 统一用 bridge；WSL 原生 dockerd 若 NAT 缺失，跑 `bash install.sh fix-network`，不要改 host |
| `RAW_RPM_PER_CLIENT` | `30` | 按客户端限流（0=不限），对齐 GLM 免费档 |
| `RAW_UPSTREAM_TIMEOUT_S` | `60` | 上游全局超时，覆盖 GLM-4-9B-0414 冷启动 |
| `RAW_MODEL_TIMEOUTS` | 见 `.env.example` | 按模型分级超时的 JSON（快模型 12s、慢模型 45–50s）；未命中用全局值 |
| `WARMUP_MODELS` | 空 | 启动预热模型（逗号分隔）；会消耗免费额度，默认关闭 |
| `SAFESHELL_API_TOKEN` | 空 | 可选共享令牌；**默认空=零配置不鉴权**，公开部署前务必设置。客户端用 `SHELLMATE_CLOUD_TOKEN` |
| `DEMO_MODE` | `true` | 演示模式开关（compose 里覆盖为 false） |
| `DB_PATH` | `./data/margin.db` | 容器里为 `/app/data/margin.db` |

---

## 5. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 双击 `install.bat` 提示 WSL 未就绪 | WSL 未启动 / 首次唤醒 | 先手动运行一次 `wsl`，再双击 |
| 提示 `docker daemon unreachable` | WSL 隔夜休眠，dockerd 未自启（`/etc/profile.d` 只在 login shell 触发自启） | 在 WSL 里执行 `service docker start`；一键脚本已用 `bash -lc` + 重试 + 自动拉起处理 |
| **Windows 侧间歇 502 / 连接被重置，容器日志反复出现 "Started server process"** | WSL2 **没有常驻会话时实例反复休眠/重建**，容器随之重启（与网络模式无关） | **双击 `keep-wsl.bat` 保持一个常驻 WSL 终端窗口（最小化即可）**，见 §1.2；脚本每 30s 自愈 dockerd/容器/health。或改用 Docker Desktop（bridge 正常） |
| 容器内 `pip install` / `npm ci` 解析不了域名；bridge 容器访问不了厂商 API | dockerd 以 `--iptables=false` 启动，缺 MASQUERADE（出站）与 DNAT（端口映射） | **已修复**：去掉禁用参数、切 iptables-legacy、重启 dockerd。新环境执行 `bash install.sh fix-network` 兜底；**不要**再退回 host 模式 |
| 提示 `Published ports are discarded` | 仅 `SFP_NETWORK_MODE=host` 时 compose 忽略 `ports` | 当前默认 bridge，不应出现；若出现说明误配，改回 bridge 并跑 `fix-network` |
| 端口 8772 被占用 | 其他服务 | `install.sh doctor` 检查；或换 `APP_PORT`（并同步 §4.3） |
| 模型返回 `403 / MODEL_DISABLED` | 管理员停用了该模型 | 控制台「免费模型清单」点「启用」，或改用其他模型 |
| `429 / MODEL_COOLDOWN` | 该模型刚超时/过载，自动熔断中 | 无需处理，客户端会自动切下一个；5 分钟后自动恢复 |
| `429 / RATE_LIMITED` | 单客户端超过 30 RPM | 稍后重试，或调 `RAW_RPM_PER_CLIENT` |
| `RAW_TIMEOUT` / `RAW_HTTP_5xx` | 上游过载或冷启动 | 已自动熔断并切换；首次遇到可能等最多 60s |
| `glm-4.7-flash` 一直失败 | 智谱免费档长期过载 | 观察几天；如持续不可用，在控制台人工停用 |
| 客户端报「行协议不完整」 | 部分开源模型输出不合规 | 客户端会自动换下一个候选模型；全部失败才报错 |
| 页面顶栏一直 `DEMO` | 未配置真实 Key | 走 §2 向导接入 |
| 密钥疑似泄露 | — | 立刻到厂商控制台吊销并重建，删除 `data/margin.db` 重新配置 |

查看日志：`bash install.sh logs`，或 `docker compose logs --tail 100 app`。

---

## 6. API 速查（`http://127.0.0.1:8772`）

| 方法 | 端点 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查（容器 healthcheck 使用） |
| POST | `/api/v1/raw/completions` | **纯模型透传**：OpenAI 兼容 `messages`，按 `model` 路由，带限流与审计；供 ShellMate 使用 |
| GET | `/api/v1/models` | 模型清单（接入/启用状态、停用原因、熔断剩余秒数、近 1h 延迟与成功率） |
| PUT | `/api/v1/models/status` | 启用 / 停用模型，body `{"model":...,"enabled":false,"reason":...}` |
| GET / PUT | `/api/v1/policy` | 读取 / 更新团队安全策略 |
| GET | `/api/v1/audit/report` | 成本节省报告（可按 `client_id` 过滤） |
| GET | `/api/v1/audit/logs` | 审计明细（含风险等级、命令哈希、哈希链字段） |
| GET | `/api/v1/audit/verify` | 校验审计哈希链完整性，返回首个异常行 id |
| GET | `/api/v1/audit/export?format=csv\|jsonl` | 导出审计明细（归档 / 离线复核） |
| GET | `/api/v1/metrics` | Prometheus 文本指标（调用量、错误、延迟、按模型成功率、熔断数） |
| GET | `/api/v1/connect/presets` · `/status` | 可接入平台清单 / 脱敏接入状态 |
| POST | `/api/v1/connect/test` · `/configure` | 测试连接 / 保存启用 |
| DELETE | `/api/v1/connect/{id}` | 移除接入配置 |
| POST | `/api/v1/chat` · `/api/v1/chat/completions` | 统一对话网关（六步流水线） |
| GET | `/api/v1/overview` · `/providers` · `/strategy` · `/events` · `/logs` · `/discover` | 额度总览 / 数据源 / 策略 / 事件 / 日志 / 政策检索 |
| POST | `/api/v1/switch` · `/simulate/429` · `/refresh` · `/feedback` | 切换 / 429 演练 / 手动轮询 / 反馈 |
| GET | `/docs` | Swagger（每个接口含说明与示例） |

调用 raw 端点的请求头：`X-Client-ID`（限流与审计归属）、`X-ShellMate-Mode`（`NL2Cmd` / `Cmd2NL`）；若服务端设置了 `SAFESHELL_API_TOKEN`，还需 `X-API-Token`（或 `Authorization: Bearer`）。

示例：

```bash
curl -X POST http://127.0.0.1:8772/api/v1/raw/completions \
  -H "Content-Type: application/json" -H "X-Client-ID: my-team" \
  -d '{"model":"glm-4-flash","messages":[{"role":"user","content":"Reply with exactly OK"}]}'
```

---

## 7. 安全与隐私边界（请务必读完）

- **不执行、不隔离**：所有命令只是文本；是否执行、在哪执行由你决定。
- **本地优先**：装了 Ollama 时提问不出本机；只有显式开启 `SHELLMATE_CLOUD_FALLBACK` 且本地不可达才走云端，并常驻提示。
- **密钥**：只存服务端本机 SQLite，页面/日志仅脱敏；镜像内零凭据（`.dockerignore` 已排除 `data/`）。共用电脑或生产环境请改用密钥管理服务。
- **策略只收紧**：客户端拉取策略失败会静默降级为「无额外策略」，但**本地 `gate.py` 的确定性门控永远在线**，不会因为拿不到策略而放行。
- **额度表/趋势仍是演示数据**：真实余额、限流与账单请到厂商控制台查看。
- **政策清单是静态快照**（2026-11-13 核实），免费政策变化快，用前请点官方链接复核。
- **审计**记录模型、模式、token、延迟、成败与估算节省；**命令原文绝不落库**，只存 `risk` 等级与生成命令的 SHA-256（`command_hash`）。每行带 `prev_hash` / `row_hash` 组成哈希链，可用 `/audit/verify` 检测篡改；升级前的历史行会被标为 `legacy_rows`，不误报。
- **公开部署**：默认不鉴权、只适合本机 / 内网。若要把 8772 暴露到公网，务必设置 `SAFESHELL_API_TOKEN` 并给每个客户端分发令牌，否则任何人都能改策略、看审计、用光你的免费额度。
- **无 SLA、无官方托管**：社区自部署，可用性取决于你自己的机器与厂商免费档；WSL 用户需按 §1.2 保持常驻窗口。

---

## 8. 附录：文件与端口速查

| 项 | 位置 / 值 |
|---|---|
| 主工作区 | **`/root/safeshell-pro`**（WSL2 ext4）；Windows 侧原目录为只读归档 |
| 一键安装 | `bash install.sh`（WSL 主入口）；`install.bat` 为 Windows 转发器（legacy） |
| WSL 常驻保活 | `release/mc_src/keep-wsl.bat`（双击）、`keepalive.sh`、`install.sh keepalive` |
| 许可证 | `release/mc_src/LICENSE`（AGPL-3.0 全文） |
| 网关源码 | `release/mc_src/server/`（`raw.py` 透传、`db.py` 审计哈希链、`cache.py` 缓存、`rate_limit.py`；`gate` 在客户端侧） |
| 前端源码 | `release/mc_src/web/`（构建产物 `web/dist/`） |
| 客户端 | `ShellMate-V1.0-交接包/shellmate/`（`start.py` 入口；`policy.py` 策略与候选评分、`eval/cloud_eval.py` 质量评测） |
| 质量 baseline | `ShellMate-V1.0-交接包/shellmate/eval/baseline_cloud.json` |
| 服务端口 | **8772**（禁用 8000；开发前端独立时 21080） |
| 端口台账 | `ai-fullstack-delivery/PORTS.md` |
| 镜像 / 容器 | `mc_src-app:latest` / `safeshell-pro-app` |
| 测试 | 网关 `pytest -q`、客户端 `python -m pytest tests/ -q`（数字见 `CHANGELOG.md` / 方案文档 §12） |

---

_本手册随代码同步更新；若命令与界面不一致，以 `release/mc_src/README.md` 与 `CHANGELOG.md` 为准。_
