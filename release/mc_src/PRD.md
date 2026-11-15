# 余量 · FREE TIER ROUTER（全栈版）PRD v0.1.0

> 状态：reviewed ｜ 负责人：项目组 ｜ 日期：2026-11-12
> 本文件对应 `ai-fullstack-delivery` S1 Gate。上一代产物是 `../` 下的零依赖前端原型（DEMO 数据、刷新即重置），本项目是**新建的正式全栈工程**：真实的服务端状态、持久化、配额轮询、路由状态机与可回溯日志；默认使用可复现的本地 mock 上游，政策知识库为官方核实快照，并支持由使用者填入自己的 API Key 后切换到真实厂商（v0.2.0 起）。

## 1. 一句话定义

为**同时接入多个免费大模型 API 的独立开发者 / 小团队后端工程师**解决「免费额度分散、耗尽才发现、429 导致请求中断」的问题，通过**统一额度监控 + 耗尽前预警 + 耗尽后自动切换并重放请求**，使其在**单次请求 p95 < 3 s、切换耗时 < 500 ms** 内得到**不中断的模型回答与可追溯的切换记录**。

## 2. 用户画像 Persona

| 项 | 内容 |
| --- | --- |
| 身份 | 工作 2–5 年的独立开发者 / 小团队后端工程师，自己写 AI 应用的网关层，同时注册了 3–9 家提供免费额度的大模型 API |
| 典型场景 | 本地或小流量线上服务跑 Agent / 对话功能，白天高频调用，某一家的 RPD / TPM 免费额度突然打满，客户端收到 429 |
| 能力边界 | 熟悉 HTTP / Python / JS，能配置环境变量与读日志；**不熟悉**各家厂商 Usage API 的差异、重置时区和限流语义；不会为了额度专门做一套监控 |
| 发生频率 | 每周 2–4 次额度耗尽或限流，月末 / 演示前更集中；每次排查与手工换 Key 约 10–20 分钟 |
| 次要画像 | 做技术选型的技术负责人，需要横向对比「哪家还有免费额度、限速多少、接入门槛」，每周 1 次 |

## 3. 核心痛点 Pain Point

| 编号 | 现状行为 | 耗时 / 失败率 | 为什么难 |
| --- | --- | --- | --- |
| P-01 | 收到 429 后临时翻厂商控制台查额度，再手改代码里的 model / base_url | 10–20 分钟 / 次 | 各家 Usage API、单位（RPD/TPM/tokens/月）、重置时区都不同，无法直接比较 |
| P-02 | 靠记忆或便签记「哪家还有免费额度」，耗尽当天才发现 | 中断率不可控 | 没有统一阈值预警，也没有按剩余额度排序的备胎列表 |
| P-03 | 手工换模型后重新发起对话，上下文丢失 | 用户可感知 | 切换发生在客户端配置层，而不是可重放的服务端网关层 |
| P-04 | 出问题后无法回答「刚才到底走的哪个模型、扣了谁的钱」 | 排查 30 分钟+ | 日志里没有 `request_id / model / prompt_version / token_usage` 的关联 |

### 现有方案与失效原因

| 方案 | 失效原因 |
| --- | --- |
| 厂商各自控制台 | 多站点切换、单位不统一、无跨厂商排序、无 API 可拉取 |
| 自己在代码里 try/except 429 | 只解决「已失败」不解决「提前预警」；换模型常丢上下文；无统一日志 |
| 通用 API 网关 / 负载均衡 | 面向付费 Key 与健康检查，不理解「免费额度」与「每日重置」语义 |
| 直接问通用 LLM「哪家还有免费额度」 | 政策随厂商变化、无来源、无法得到本工作区真实的剩余额度 |

## 4. 用户故事与验收标准

| ID | 用户故事（As a / I want / so that） | Given-When-Then 验收标准 |
| --- | --- | --- |
| US-01 | 作为开发者，我想在一个表里看到所有数据源的剩余免费额度与状态，以便决定用哪家 | Given 服务端已启动且 `providers` 有 9 条种子数据，When 打开额度总览或调用 `GET /api/v1/overview`，Then 返回统一单位口径的 `used/limit/remaining/unit/reset_at/status`，且前端 6 列额度表与 KPI 与接口一致 |
| US-02 | 作为开发者，我想在剩余额度低于阈值时收到预警，以便提前处理 | Given 策略阈值 10% 且某源剩余 8%，When 一次轮询 `check_quota()` 执行，Then 产生 1 条 WARN 事件 + 1 条结构化日志 + SSE `event: warning`，同一状态重复轮询不重复告警（去重标记落库） |
| US-03 | 作为开发者，我想在当前源耗尽时自动切换到备胎且不中断请求，以便用户无感 | Given 当前源剩余 0 且 `auto_switch=true`、备胎链路有可用源，When 发送一次 chat 请求或调用 `POST /api/v1/simulate/429`，Then `request_id` 保持不变、自动 `switch_to()` 后重放成功返回 `200`，响应含 `fallback_from/routed_by`，对话流插入 system 提示卡说明上下文保留并可撤销 |
| US-04 | 作为开发者，我想看到真实的请求阶段而非转圈，以便知道卡在哪一步 | Given 请求进行中，When 前端订阅 `GET /api/v1/events/stream`，Then 依次收到 `parse → retrieve → quota_check → route → generate → respond` 中实际执行的 stage 事件（检索为空时不会收到 `generate`），每阶段带耗时，禁止前端定时模拟 |
| US-05 | 作为开发者，我想在模型超时时知道发生了什么并可重试，以便不面对白屏 | Given 上游 mock 注入超时，When 超过 `LLM_TIMEOUT_MS`，Then 返回 `error_code=LLM_TIMEOUT` + 兜底文案，前端异常态显示【重试】【反馈】且保留原输入，日志含 `request_id` |
| US-06 | 作为开发者，我想让「免费额度问答」有来源可查，以便判断可信度 | Given 提问命中本地政策知识库，When 渲染回答，Then 每条结论带 `sources[]`（ID 必须属于本次检索命中集合）；Given 检索为空，When 执行流水线，Then **短路返回** `error_code=NO_CONTEXT`，不调用 LLM、不编造政策 |
| US-07 | 作为开发者，我想把密钥放在服务端，以便前端不泄露凭据 | Given `.env` 配置了上游 Key，When 前端请求任意接口，Then 响应体中不含明文 Key，仅出现 `sk-••••7f3a` 形式的脱敏值，日志脱敏 |
| US-08 | 作为开发者，我想一键注入 429 演练，以便验证切换链路 | Given 控制台页，When 点击【注入一次 429】，Then 依次产生 `429 → quota_exhausted → auto_switch → replay 200` 的事件流与日志，切换次数与事件流均 +1 |

## 5. MVP 功能列表与边界

| ID | 功能 | 验收标准 | MoSCoW |
| --- | --- | --- | --- |
| F-01 | 额度总览（9 个数据源、统一单位、6 列表格、KPI、24h 趋势） | 与 `/overview` 返回一致；6 s 轮询由服务端后台任务驱动，失败按退避重试 | Must |
| F-02 | 阈值预警与耗尽去重 | 见 US-02；重复轮询不重复告警 | Must |
| F-03 | 自动切换状态机（`check_quota` / `pick_next` / `switch_to`） | 见 US-03；全部耗尽时保持当前源不抖动 | Must |
| F-04 | 统一 chat 网关（显式 timeout / temperature、429 重放、上下文保留） | 单次调用显式 `timeout=10s`、`temperature=0.2`、`max_tokens=512`；幂等 `request_id` | Must |
| F-05 | 六步 Agent 流水线与 SSE 真实阶段 | parse / retrieve / quota_check / route / generate / respond，每步有独立失败分支 | Must |
| F-06 | 超时、限流、解析失败、空检索四类兜底 | `LLM_TIMEOUT / RATE_LIMITED / PARSE_ERROR / NO_CONTEXT` 各有 error_code、兜底文案与可点击动作 | Must |
| F-07 | 免费 API 政策知识库检索与来源溯源 | Top-K 命中才生成答案，`sources[]` 校验归属；空结果短路 | Must |
| F-08 | 调用控制台 / 对话测试台 / 429 演练 | 请求配置、响应 JSON、实时日志、会话 system 提示卡与撤销 | Must |
| F-09 | 结构化日志与持久化 | SQLite 存 providers / quota_snapshots / events / request_logs / conversations；日志含 `request_id/prompt_version/model/latency_ms/token_usage` | Must |
| F-10 | 真实厂商 Usage API 适配器 | 接口已抽象，但本期只实现 mock 与 HTTP 适配器骨架，不实装各家鉴权差异 | Should |
| F-11 | 多工作区 / 账号权限 / 邮件与 Webhook 真实投递 | 本期不做，仅保留通知渠道开关状态 | Won't |
| F-12 | 额度耗尽时间预测、向量检索、移动端原生 | 本期不做 | Won't |

### 明确不做（Out of scope）

- **真实厂商计费与 Usage API 全量适配** —— 理由：各家鉴权与口径差异大，且需要真实 Key；本期用可复现的 mock 上游验证链路，接口留出 `HttpUsageAdapter`。
- **账号体系 / 多租户 / RBAC** —— 理由：单开发者工作台，引入后评测口径会被权限分支污染。
- **真实邮件 / Webhook 投递** —— 理由：外部副作用不可回滚，本期只在事件流中记录通知意图。
- **向量数据库 / 语义检索** —— 理由：政策知识库仅 12 条结构化文档，关键词 + metadata 过滤足够且可复现；在 EVAL_REPORT「已知局限性」登记。
- **移动端原生应用 / IDE 插件** —— 理由：分散 MVP 验证焦点。
- **自动执行厂商侧变更（删 Key、改账单）** —— 理由：不可逆操作风险。

## 6. 非功能指标 NFR

| 类别 | 指标 | 目标值 | 测量方法 | 基线 / 依据 | 未达标处理 |
| --- | --- | --- | --- | --- | --- |
| 性能 | TTFT（SSE 首包 / 首阶段事件） | p95 < 800 ms | 客户端记录 `request_start` 与首个 `stage` 事件时间戳，≥20 次样本 | 无真实线上基线；目标为本地 mock 工程预算（原型仅伪计时 842 ms，不可作为基线），S4 实测后回填 | 缩短检索与 DB 查询、去掉同步阻塞 |
| 性能 | 端到端总延迟 | p50 < 600 ms、p95 < 3 s | `test_ai_performance.py` 记录 complete 响应耗时，≥20 次 | 同上 | 降 Top-K、限 `max_tokens`、异步轮询 |
| 性能 | 自动切换附加耗时 | < 500 ms（p95） | `switch_to()` 前后服务端时间戳差 | 原型状态机为内存操作，量级 <100 ms | 减少同步落库次数、加复合索引 |
| 质量 | 意图路由 / 兜底判定准确率 | ≥ 90%（`eval_dataset_v1.0`，24 条） | 按 `expected_type` 精确匹配判定（关键词命中口径），命令见 EVAL_REPORT | 基线为规则版流水线 5 条人工试跑（S4 前补测并回填） | 补规则 / 调 Prompt / 改检索阈值 |
| 质量 | 修复 / 操作建议可用率 | ≥ 80% | rubric：回答中至少 1 步能在当前环境直接执行，且总步数 ≤ 3 | 无历史数据，属首版验收线 | 压缩回答模板、固定步骤上限 |
| 成本 | 单轮 Token | < 500 tokens（prompt + completion + 检索片段） | 日志 `token_usage` 求和，模型与计数口径见附录 | mock 上游按字符估算，非厂商 tokenizer | 限 history 轮数、Top-K=3、`max_tokens=512` |
| 安全 | 违规输入拒绝率 | 100%（评测集 safety 类用例） | `pytest -q tests/test_ai_performance.py -k safety` | — | 前置规则 + 后置过滤双保险 |
| 安全 | 明文密钥泄露 | 0 次 | 扫描所有 API 响应体与日志文件，正则 `sk-[A-Za-z0-9]{20,}` | — | 脱敏中间件 + 启动时环境变量 fail-fast |
| 可用性 | 异常兜底覆盖率 | 100%（超时 / 限流 / 解析失败 / 空输出 / 空检索） | 故障注入测试 `tests/test_failure_injection.py` | — | 补齐异常分支 |

### 测量口径附录

- 测试环境：Windows 11 / Python 3.13.14 / Node 22.22.2 / SQLite（本地文件）/ 无外部网络调用（`PROVIDER_MODE=mock`）。
- 模型与参数：默认上游 `mock-deepseek-v3`，`temperature=0.2`、`max_tokens=512`、`timeout=10 s`；Prompt 版本 `quota-router-cn-v1.0.0`。
- 样本量与时间窗：≥20 次请求，2026-11-12 本次交付窗口内一次性测得，测量命令与原始结果见 `EVAL_REPORT.md`。
- 数据集版本：`tests/eval_dataset.jsonl`（`eval_dataset_v1.0`，24 条，四层分层）。
- TTFT 与总延迟**分开定义、分开测量**：TTFT = 请求发出 → 首个 SSE stage 事件；总延迟 = 请求发出 → `respond` 完成。
- rubric 定义：意图类型匹配 + 结论不与 `sources` 冲突 + 至少 1 步在当前环境可直接执行 + 步骤 ≤ 3。
- 准确率判定为**关键词 / `expected_type` 精确匹配**，非 LLM-as-judge、非人工评分；`temperature=0.2` 在 mock 上游下为确定性输出，回归测试固定 seed。

## 7. 交互原型三态

| 状态 | 文件 | 触发条件 | 主要动作 | 对应后端阶段 / error_code |
| --- | --- | --- | --- | --- |
| 正常 | `docs/prototypes/prototype-normal.png` | `respond` 成功 | 复制代码 / 重新生成 / 查看来源 | `parse → retrieve → quota_check → route → generate → respond` |
| 加载 | `docs/prototypes/prototype-loading.png` | 请求进行中 | 逐阶段文案 + 每阶段耗时；>8 s 追加「仍在生成」 | SSE `stage: parse/retrieve/quota_check/route/generate` |
| 异常 A | `docs/prototypes/prototype-error.png`（左） | 上游超时 / 5xx | 【重试】【反馈】 | `LLM_TIMEOUT` |
| 异常 B | `docs/prototypes/prototype-error.png`（右） | 幻觉 / 无来源 / 违规输入 | 【反馈】【查阅官方文档】 | `NO_CONTEXT` / `UNSUPPORTED_CLAIM` |

三个状态共用同一份文案配置（`web/src/stageText.ts`）；异常态保留用户原输入，按钮绑定真实动作（重试重新发同一 `request_id` 的原输入、反馈写入事件流）。

## 8. 假设、风险与依赖

| 类型 | 内容 | 应对 |
| --- | --- | --- |
| 假设 | 用户接受「默认 mock 上游、可离线复现」，真实厂商需自行填 `.env` 并实装适配器 | README 首屏写明 DEMO 边界；`PROVIDER_MODE` 显式可切 |
| 假设 | 免费额度可以按统一 `unit + reset_at` 归一化 | `usageLabel()` 统一记法；混单位的数据源在表内标注原始单位 |
| 风险 | 外部 LLM / Usage API 可用性与限流 | `timeout=10s` + 指数退避 + 分类 error_code 兜底 |
| 风险 | 政策知识库仅 12 条本地文档，且为演示数据，不代表厂商当前政策 | 界面标 DEMO；EVAL_REPORT「已知局限性」原样承接 |
| 风险 | 评测集由开发者构造，存在偏差与泄漏风险 | Few-shot 与评测集分离；版本化数据集；上线后收集真实 Bad Case |
| 风险 | 无真实用户流量，延迟指标只代表本地环境 | 明确标注测量环境，不与线上 SLA 混用 |
| 依赖 | FastAPI / Pydantic / uvicorn / httpx / pytest；Vite / React / TypeScript；SQLite 文件 | `requirements.txt` 与 `package-lock.json` 锁版本；`.env.example` 列出变量 |

## 9. 里程碑

| 阶段 | 产出 | Gate 判据 | 状态 |
| --- | --- | --- | --- |
| S1 产品定义 | 本 PRD + 三态原型 | 见 SKILL.md S1 Gate | ✅ 已完成 |
| S2 工程环境 | 锁版本依赖 + .env.example + .gitignore + Git 规范 + 容器 | 见 SKILL.md S2 Gate | 进行中 |
| S3 后端与 Agent | API 契约 + 六步流水线 + SQLite DDL + React 三视图 | 见 SKILL.md S3 Gate | 进行中 |
| S4 测试 | 评测集 + pytest + EVAL_REPORT + Bad Case | 见 SKILL.md S4 Gate | 待开始 |
| S5 交付 | README / CHANGELOG / Swagger / 日志 / 审计报告 | `delivery_audit.py` 无 FAIL | 待开始 |
