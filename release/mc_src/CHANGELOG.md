# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。
**凡影响模型输出的变更（Prompt、模型、参数、Embedding、检索阈值、知识库、评测口径）都必须记录**，否则无法回溯「回答变笨」。
版本号与 git tag 一一对应：`v0.1.0` ↔ `[0.1.0]`。

## [Unreleased]

### Changed（2026-11-15 · 产品定位）

- **完全免费 · AGPL-3.0 全量开源**：不收费、不设付费档、不提供官方托管与 SLA。新增 `LICENSE`（AGPL-3.0 全文）；落地页三档定价改为「完全免费 · 没有付费版」说明卡；团队控制台标题与文案去掉商业化口径。所有功能一次给全。
- **默认停用 `glm-4.7-flash`**（影响模型输出，记录于此）：智谱免费档长期 429 / 错误码 1305 且挂起至超时，实测拖慢全链路到 66s。`db.DEFAULT_DISABLED_MODELS` 让它在无显式 `model_status` 记录时默认停用，`/api/v1/models` 返回停用原因；管理员可在控制台「启用」覆盖。ShellMate 默认 light / deep 候选链与 `cloud_model_deep` 均移除该模型（当前默认启用 5 个模型）。

### Added（2026-11-15 · 改进路线落地）

- **WSL 常驻保活**：`keepalive.sh` + `keep-wsl.bat`（双击后最小化常驻），`install.sh keepalive | keepalive-once | keepalive-stop`。守护 dockerd + 容器 + health（30s 一轮），消除 WSL 实例休眠导致的容器重启与 Windows 侧间歇 502。
- **审计哈希链（P0-3）**：`raw_audit` 增加 `prev_hash` / `row_hash`（`row_hash = sha256(prev_hash + 规范化本行内容)`），旧库自动迁移补列；新增 `GET /api/v1/audit/verify`（篡改检测，历史无哈希行标记 `legacy_rows` 不误报）与 `GET /api/v1/audit/export?format=csv|jsonl`。raw 端点现在填充 `risk` 与 `command_hash`（只存 SHA-256，绝不存命令原文）。
- **可选共享令牌（P0-1 轻量版）**：`SAFESHELL_API_TOKEN` 默认空 = 零配置；设置后除 `/health` 外所有 `/api/v1` 端点需 `X-API-Token` 或 Bearer。客户端 `SHELLMATE_CLOUD_TOKEN`、前端控制台「访问令牌」输入框。
- **性能与度量（P1-1 / P1-2 / P1-3）**：新增 `server/cache.py`，`/overview` 与 `/models` 3s TTL 缓存（写操作自动失效）；`RAW_MODEL_TIMEOUTS` 按模型分级超时、`WARMUP_MODELS` 可选预热；`/models` 返回近 1h 平均延迟与成功率，ShellMate 据此对候选链做稳定降权；ShellMate 新增 `eval/cloud_eval.py` 云端真实模型质量评测（可用率 / 行协议合规率 / 风险一致率 / p50·p95）；网关新增 `GET /api/v1/metrics` Prometheus 文本指标。
- **测试**：新增 `tests/test_improvements.py`（13 项）、ShellMate `tests/test_policy_scoring.py`（6 项）。

### Added（此前）：`install.bat`（Windows 双击，通过 `WSLENV /p` 转换含中文的路径、login shell 触发 WSL 的 dockerd 自启）、`install.sh`（WSL / Linux / macOS，子命令 `install / status / logs / stop / restart / doctor`）。自动识别 WSL2 原生 dockerd 并写 `SFP_NETWORK_MODE=host`、生成 `.env`、构建、启动、等健康检查。
- **交付文档**：工作区根新增 `SafeShellPro-User-Manual.md`（安装 / 使用 / 运维 / 排障 / API / 安全边界）与 `SafeShellPro-评估与改进路线.md`（实测性能、适用群体、Docker 论证、P0–P2 改进步骤）。
- **README 口径对齐**：一键启动改为 `install.bat` / `install.sh` 与端口 **8772**（原写 8000 / v0.2.0 / 7 家平台），补全 `SFP_NETWORK_MODE` / `RAW_RPM_PER_CLIENT` / `RAW_UPSTREAM_TIMEOUT_S` 环境变量与目录结构。

- **多免费模型路由 + 模型停用能力**（影响模型输出，记录于此）：
  - 接入硅基流动 4 个实测免费模型 `Qwen/Qwen3-8B` / `Qwen/Qwen2.5-7B-Instruct` / `THUDM/GLM-4-9B-0414` / `deepseek-ai/DeepSeek-R1-0528-Qwen3-8B`，与智谱 `glm-4-flash` / `glm-4.7-flash` 共 6 个；`connect_presets.json` 新增 `models` 数组表达「一平台同 Key 服务多模型」。
  - `connect.resolve_credential(model)` 按模型名解析凭据（api_base / api_key），raw 端点不再依赖单一 `active_provider`；`connect.all_model_entries()` 生成模型级去重清单。
  - 新增 `model_status` 表、`GET /api/v1/models`（清单 + 启用状态）、`PUT /api/v1/models/status`（启用/停用，原因留痕）；停用模型经 raw 调用返回 `403 / MODEL_DISABLED`。
  - `RAW_UPSTREAM_TIMEOUT_S`（默认 60s）适配 GLM-4-9B-0414 等冷启动较慢的开源模型（原写死 30s 会误判超时）。
  - 前端「团队控制台」新增免费模型清单卡片，支持一键停用/启用与健康度（熔断）展示；落地页模型说明同步为 6 个。
  - **模型健康度自动熔断**：新增 `model_health` 表；上游 `RAW_TIMEOUT` / 429 / 5xx 触发 5 分钟冷却，冷却期内网关毫秒级返回 `429 / MODEL_COOLDOWN`，客户端立即切下一个候选（避免每次白等 60s 超时）；到期自动恢复，成功或人工启用即清除。`/api/v1/models` 新增 `cooldown_remaining`。
  - **WSL2 Docker 适配**：`docker-compose.yml` 构建期 `build.network: host`、运行期 `SFP_NETWORK_MODE` 可切 `host`（bridge 出站 DNS 不通时）；新增 `.dockerignore` 排除含真实 Key 的 `data/` 与大体积依赖；客户端候选链把最稳最快的 `glm-4-flash` 提到深度链第二位。
  - Docker 镜像已在 WSL（Docker 29.8.2）实构并端到端验证：health / 前端托管 / 11 项模型清单 / GLM 与 SiliconFlow 真实转发 / 审计落库 / ShellMate 全链路均 OK；熔断生效后端到端耗时 66s → 5.2s。
  - ShellMate 客户端：新增候选模型链（`SHELLMATE_CLOUD_MODELS_LIGHT` / `_DEEP`），任一模型停用 / 429 / 5xx / **输出不合严格行协议**时自动切下一个；新增 `src/shellmate/policy.py` 拉取团队策略与模型清单，在 `apply_gate()` 之后只收紧地叠加禁用正则与风险阈值（DENY 清 `command`），拉取失败静默降级。云端错误话术不再误显 Ollama 指引。
- **纯模型透传端点** `POST /api/v1/raw/completions`：把 OpenAI 格式 `messages` 原样转发到当前已接入的免费模型上游，不经本网关的检索 / 路由 agent，只做模型路由 + 鉴权 + 原样返回。供 ShellMate 这类「本地优先、云端兜底」客户端使用；未接入任何真实模型（演示模式）时返回内置、明确标注 `demo=true` 的行协议 mock，便于整条链路联调，真实 Key 接入后无需改代码即自动走真实上游。（此端点不注入本网关的 Prompt / 检索，不触发「影响模型输出须记录」约束。）
- **SafeShell Pro 商业能力（合并 ShellMate 后）**：
  - 审计落库：`server/db.py` 新增 `raw_audit` 表（客户端 / 模型 / 模式 / token / 延迟 / 成败 / 节省额），raw 端点每次调用落明细；`GET /api/v1/audit/logs` 可回溯。
  - 成本与节省报告：`server/cost.py` 估算「免费档相对付费等价模型」的节省额（实付恒为 0）；`GET /api/v1/audit/report` 按客户端 / 模型 / 模式聚合调用量、token、延迟与节省额。
  - 按客户端限流：`server/rate_limit.py` 进程内令牌桶（默认 30 RPM，对齐 GLM 免费档），`RAW_RPM_PER_CLIENT` 可配；触发返回 429 / `RATE_LIMITED`。
  - 团队安全策略：`server/db.py` 新增 `team_policy` 表；`GET/PUT /api/v1/policy` 管理禁用命令正则、最高风险放行等级、模型白名单、最大 token、是否强制本地门控；客户端拉取后本地强制执行。
  - 前端：新增「团队控制台」（用量 / 成本 / 审计 / 策略编辑器）与「产品与定价」落地页（Open-Core 三档），品牌更新为 SafeShell Pro，`web/dist` 重新构建。
  - 部署：Dockerfile 改为多阶段（前端构建进镜像），单端口 **8772** 同时托管 API 与前端；`docker-compose.yml` 端口 8772、`healthcheck` 同步；`.env.example` 端口与 CORS 对齐 8772。端口已登记于 `ai-fullstack-delivery/PORTS.md`。

---

## [0.2.0] - 2026-11-13

面向「小白开箱能用」：真实政策清单 + 真实接入能力 + 页内四步引导。

### Added

- **真实政策清单**：`server/data/free_tier_catalog.json`（15 条），逐条联网核实厂商官方定价 / 限流 / 计费文档，带 `source_url`、`last_verified=2026-11-13` 与 `verification` 级别；9 条来自官方文档 / 官方 PDF 直证，第三方汇总单列标注、不冒充官方承诺。`docs/FREE_TIER_CATALOG.md` 为人类可读表格，含「已回收的免费层」负向清单。
- **知识库版本化**：`KNOWLEDGE_VERSION = free-tier-2026-11-13`，`knowledge_docs` 按 version 隔离，旧版本保留但不参与检索；`db.init_db()` 按版本 seed，不再依赖 `data/` 目录被跟踪。
- **真实接入能力**：`server/data/connect_presets.json`（7 家 OpenAI 兼容平台，含 base_url、默认免费模型、官方注册 / Key / 文档链接与手把手步骤）、`server/connect.py`（凭据管理、脱敏、连接测试）、5 个 `/api/v1/connect/*` 接口。
- **按厂商路由**：`HttpLLM` 改为按 `api_base / api_key / model` 实例化；`get_llm()` 支持 `provider_id` 与「当前启用」配置；`ChatRequest.provider_id` 可指定真实上游，未配置自动回落演示模式。
- **宽松后处理**：`postprocess_plain()` 允许真实模型返回自然语言，不因「非 JSON / 未引用本地文档」被判 `UNSUPPORTED_CLAIM` 或 `PARSE_ERROR`；mock 仍走严格契约保证回归测试。
- **新手接入向导**：`web/src/components/Onboarding.tsx` 四步流程（模式说明 → 选平台 → 填 Key 测试保存 → 发真实消息）；首屏常驻 `GuideBanner`；保存即自动启用，顶栏 `DEMO → LIVE`；可一键移除回到演示模式。
- **测试**：`tests/test_live_upstream.py` 用本地假 OpenAI 兼容服务端到端验证「测试连接 → 保存 → 真实对话 → 移除」；新增 4 项接入契约测试；`smoke_startup.py` 增加 2 项接入检查。

### Changed

- **口径变更，历史数字不可直接比较**：评测集 `eval_dataset_v1.0` → `v1.1`，24 条用例全部改为真实政策问答（Gemini / Groq / OpenRouter / Cohere / Cloudflare / 智谱 / 百炼 / TokenHub / NVIDIA / Mistral / 已回收清单），`expected_keyword` 随政策数字重写。
- **影响模型输出**：知识库从 12 篇编造 DEMO 文档换成 15 条官方核实快照；`MockLLM` 答案改为取首个命中前 80 字（原为两条各 45 字），信息更完整且仍守住 500 token 预算；答案注明「以厂商官方文档为准」。
- `DiscoverOut` 增加 `catalog` 元信息；`OverviewOut` 增加 `active_provider`，`demo_mode` 随接入状态动态变化。
- 发现抽屉「一键接入」更名为「加入本地额度表」，并明确标注只新增演示记录、不配置密钥、不发起真实调用，避免误导。

### Fixed

- 移除全部编造的 `https://docs.local/demo/...` 占位链接与内部条目的假 GitHub URL；内部 runbook 改为仓库内文档引用，前端只对真实外链渲染跳转。
- 真实上游返回自然语言时不再报 `PARSE_ERROR`；本地回环上游（Ollama / 自建网关）不再被系统代理拦截。
- Dockerfile 干净 clone 构建、空 `web/dist` 静态挂载保护（v0.1.0 已修，本版回归覆盖）。

### Eval

- 命令：`pytest -q`、`python -m tests.measure_sse`、`python -m tests.measure_performance`、`scripts/eval_runner.py`、`scripts/smoke_startup.py`、`scripts/delivery_audit.py --project .`。
- 指标见 `EVAL_REPORT.md` 与 `docs/eval_metrics.json` / `docs/sse_metrics.json`；因评测集口径变更，准确率数字**不可与 v0.1.0 直接比较**。
- 已知局限：额度表 / Usage 轮询仍是演示数据；政策清单是静态快照、运行时不联网；真实接入需用户自己的 API Key，各家 OpenAI 兼容程度不一，故以「测试连接」为启用门槛。

---

## [0.1.0] - 2026-11-13

首个可交付版本：`余量 · FREE TIER ROUTER` 全栈实现（S1–S5 五阶段门禁全部通过）。

### Added

- **产品定义**：`PRD.md`（Persona / Pain Point / 6 条含 Given-When-Then 的用户故事 / MVP In-Out / 7 项可量化 NFR）；`docs/prototypes/` 三态原型（normal / loading / error PNG + 可复现 HTML），加载态六阶段文案与后端 SSE 一一对应。
- **工程环境**：`requirements.txt` / `requirements-dev.txt` 全 `==` 锁版本、`.python-version`（3.13.14）、`.env.example`、`.gitignore`、`Dockerfile`、`docker-compose.yml`、`pytest.ini`；venv 可复现安装；独立 git 仓库 + Conventional Commits。
- **后端**：FastAPI + Pydantic 契约（`server/models.py`）、Swagger `/docs` 请求示例、`X-Request-ID` 中间件、统一异常处理；SQLite DDL 与复合索引（`server/db.py`）、`schema.sql` MySQL 生产 DDL 参考。
- **路由引擎**：9 个 DEMO 数据源统一单位口径、额度轮询快照、阈值预警去重、`unlimited`/`limited` 状态语义、备胎链路自动切换、429 重放与「注入一次 429」演练接口。
- **Agent 六步流水线**：`parse → retrieve → quota_check → route → generate → respond`，关键词 + `lang/version` metadata 检索、检索为空短路、Prompt 版本化 `quota-router-cn-v1.0.0`、来源存在性校验、超时/限流/解析失败/安全拒绝/空输入的结构化 `error_code` 兜底。
- **可观测性**：结构化 JSON 日志（`request_id` / `prompt_version` / `model` / `temperature` / `max_tokens` / `latency_ms` / `ttft_ms` / `token_usage` / `retrieval_hits` / `sources` / `error_code`），密钥 / 邮箱 / 手机号脱敏，用户 ID 哈希；SSE 事件总线推送真实阶段与预警 / 切换通知。
- **前端**：Vite + React + TypeScript 三视图（额度总览 / 调用控制台 / 对话测试台），沿用原型深色 token；fetch 真实 API、SSE 实时阶段、异常态按 `error_code` 分发且每个场景有可点击动作；`npm run build` 产物由 FastAPI 静态托管。
- **测试与评测**：`tests/eval_dataset.jsonl`（`eval_dataset_v1.0`，24 条四层覆盖 + chitchat/refusal/no_context）、`tests/test_ai_performance.py`（43 用例）、`tests/measure_performance.py`、`tests/measure_sse.py`、`tests/eval_adapter.py`、`scripts/eval_runner.py`、`scripts/delivery_audit.py`、`scripts/smoke_startup.py`（空白数据库 + 独立 uvicorn 子进程启动冒烟）。
- **交付文档**：`README.md`（本地 + 容器一键启动、30 项环境变量表、三态与运行截图引用、API 速查）、`EVAL_REPORT.md`、`docs/BAD_CASES.md`、`docs/DELIVERY_AUDIT.md`、`docs/eval_metrics.json`、`docs/sse_metrics.json`、`docs/STARTUP_SMOKE.json`。

### Changed

- Prompt 模板文件统一命名为 `quota-router-cn-v1.0.0.txt`，与 `PROMPT_VERSION` 严格对应。
- 检索阈值 `RETRIEVAL_MIN_SCORE` 0.35 → 0.5（低分误命中修复），context 收敛到 Top-2 / 60 字以守住单轮 500 token 预算；详见 `docs/BAD_CASES.md` BC-001 / BC-002。
- **评测口径**：TTFT 由「响应体 `ttft_ms` 自估字段」改为「SSE 客户端实测首包」，响应体 `ttft_ms` 在 mock 下恒为 0 且不再作为交付口径（BC-005）。首版无历史数字，不涉及「口径变更导致不可比较」的跨版本对比。

### Fixed

- 限流中的数据源不再被误标为「已耗尽」；`unlimited` 源不再被选为切换目标（BC-003）。
- 故障注入异常分支的日志引用收口到 `_finish()`，不再有未定义引用风险（BC-004）。
- 扩充 `UNSAFE_INPUT` 规则，覆盖中文有害请求与英文 Prompt 注入（BC-006）。
- Dockerfile 干净 clone 构建：`COPY data` 改为 `RUN mkdir -p /app/data /app/logs`；`web/dist` 为空目录时不挂载静态根路径，避免 API 之外路径 404。

### Eval

- 命令：`pytest -q`、`python -m tests.measure_sse`、`python -m tests.measure_performance`、`scripts/eval_runner.py --dataset tests/eval_dataset.jsonl --agent tests.eval_adapter:run_eval_agent --sys-path . --repeat 1`。
- 意图路由 / 兜底准确率 **100.0%**（24/24）；建议可用率 **100.0%**（14/14，rubric + 人工抽检 3 条）。
- TTFT（SSE 首包，24 样本）p50 **153.3 ms** / p95 **168.2 ms**；端到端应用层延迟 p50 **62.5 ms** / p95 **75.0 ms**，真实 HTTP p50 **156.4 ms** / p95 **171.3 ms**。
- 自动切换附加耗时 p95 **60.6 ms**（20 样本）；单轮 token 平均 **237.5**、最大 **449.0**（< 500）。
- `pytest -q`：**43 passed**；`delivery_audit.py`：通过 31 项 / FAIL 0 / WARN 0；`smoke_startup.py`：13 PASS / 0 FAIL（空白库启动、Swagger、静态托管、问答、短路、429 重放、SSE 首帧、日志脱敏）。
- `docker compose config -q` 静态校验通过；本机 Docker 守护进程未运行，容器镜像**未实机构建验证**，已列入已知局限。
- 已知局限：mock 上游非流式导致 TTFT ≈ 总延迟；未接真实厂商 API；关键词检索而非向量检索；评测集由开发者构造；可用率仅单人评分；单机 SQLite；详见 `EVAL_REPORT.md` 第 6 节。

---

## 变更记录填写规则

| 变更类型 | 章节 | 必须附带 |
| --- | --- | --- |
| 新功能 | Added | 对应用户故事 ID |
| 缺陷修复 | Fixed | Bad Case 编号与复测结果 |
| Prompt / 模型 / 参数 / 检索阈值 | Changed | 版本号前后对比 + 评测前后指标 |
| 依赖升级 | Changed | 包名与精确版本前后值 |
| 评测集或口径变更 | Changed | 明确标注「口径变更，历史数字不可直接比较」 |
| 删除能力 | Removed | 迁移说明 |
