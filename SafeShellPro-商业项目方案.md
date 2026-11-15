# SafeShell Pro（壳卫）— 安全 · 免费 · 可审计的 AI 运维命令助手

> 将 **ShellMate V1.0**（本地优先 + 安全门控的命令行助手）与 **margin-console / 余量 · FREE TIER ROUTER**（免费大模型额度路由网关）融合，做成一个**完全免费、源码开放、可自部署**的 AI 运维命令助手。
>
> **2026-11-15 定位变更**：产品决定**不收费、不设付费档、不提供官方托管**，以 AGPL-3.0 完整开源全部能力（含团队控制台、审计、策略、限流）。本文保留最初的技术与实施规划作为历史记录；§4 与 §11 已按免费开源口径更新，文中残留的「商业化 / 订阅 / 定价」表述**均已作废**。

---

## 0. 一句话定位

自然语言生成 shell 命令、危险命令**硬拦截**、推理成本为 **0**、全程**可审计**——给所有人免费用、可自部署、可二次开发的开源 AI 运维助手。

---

## 1. 为什么要合并这两个项目

| 项目 | 强项 | 短板 |
| --- | --- | --- |
| **ShellMate** | 本地优先隐私、参数级安全门控（DENY 清命令）、零依赖易分发 | 只能跑本机 Ollama（需较强机器）或回退单一云端，**没有成本与团队管控概念** |
| **margin-console** | 多免费模型额度监控、耗尽自动切换、成本/用量视图 | 只是「网关基础设施」，**没有终端用户产品** |

**合并 = 完整产品栈**：客户端（安全生成命令）+ 网关（零成本推理 + 策略 + 审计）+ 控制台（团队视图）。单独用「网关」没有终端价值，单独用「CLI」又解决不了成本与团队管控；合在一起才是能真正落地、值得开源给社区用的完整工具。

---

## 2. 目标用户与痛点

- **目标**：DevOps/SRE 小团队、MSP（托管服务商）、安全敏感 SMB、受监管行业（金融/医疗/政企）。
- **痛点**：
  1. 误执行危险命令（`rm -rf`、`DROP TABLE`、密钥泄露）——真实事故高发。
  2. 云 AI 助手（Warp/Copilot）贵且成本不透明。
  3. 数据出境合规压力（提问不出机器）。
  4. 缺乏操作审计，出事无法复盘。

---

## 3. 竞品与差异化

| 竞品 | 模式 | 我们的差异 |
| --- | --- | --- |
| Warp / GitHub Copilot / Amazon Q / Codeium | 云优先、按席位较贵、门控弱 | **本地优先隐私** + **近零推理成本**（免费额度路由） + **硬安全门控** + **审计合规** |

差异化不在「模型更聪明」，而在 **省钱 + 防事故 + 可审计** 这三件企业真关心的事。

---

## 4. 产品形态（2026-11-15 起：完全免费 · AGPL-3.0 全量开源）

- **一个版本，全部功能**：CLI + 网关 + 团队控制台 + 审计哈希链 + 策略下发 + 限流 + 模型启停 + Docker 一键部署，全部 0 元、无试用期限、无席位费、无「升级解锁」。
- **不提供的东西**：官方托管服务、SLA、付费支持、闭源企业版。用户自行申请各厂商免费 API Key，在自己的机器/内网部署，数据不出自己掌控范围。
- **许可**：AGPL-3.0（全文见 `release/mc_src/LICENSE`）。可自用、可修改、可分发、可商用；网络服务形式提供时须按 AGPL 提供对应源码。
- **成本口径**：软件与免费模型实付恒为 0；报告里的 `estimated_saved_cny` 只是「若改走付费等价模型大约要花多少」的对照估算，**不是账单、也不是收费依据**。
- **社区协作**：欢迎 issue / PR / 模型可用性反馈；模型停用以各厂商当期免费政策为准，控制台可一键启停。

---

## 5. 目标架构（见附图）

- **客户端层 · ShellMate CLI**：NL2Cmd / Cmd2NL、安全门控 `gate.py`、本地缓存；团队模式下向网关鉴权、拉取策略、上报遥测（默认关）。
- **网关层 · 演进版 margin-console（单端口 8772）**：
  - 推理路由器：免费额度优先 → 便宜付费兜底，429/耗尽自动切换 + 同 `request_id` 重放（**已打通**）。
  - 策略与门控同步：网关向客户端下发可配置规则。
  - 审计日志与指标：每次调用落库（query、command、risk、model、cost、latency、user）。
  - 额度/成本监控：实时免费额度 + 成本估算 + 节省报告。
- **控制台层 · 演进版 React 前端**：团队用量、成本节省、策略编辑器、审计查看、接入向导。
- **存储**：SQLite（单机）→ PostgreSQL（企业）。
- **部署**：Docker Compose 一键起（网关 + 前端打包，单端口 8772）。

---

## 6. 端口规划（依据 `ai-fullstack-delivery/PORTS.md`）

| 用途 | 端口 | 依据 |
| --- | --- | --- |
| 网关 + 打包控制台（生产） | **8772** | 推荐段 8766–8799 内、在预留给 F3–F5 的 8767–8771 **之后**；避开全部禁用段（8000/8010/3000/3306·33060/39099–39108）与占用中 8765/8766 |
| 开发期前端（若独立起 Vite） | **21080** | 高位备选 21000–24000，避开常见 5173 |
| ShellMate CLI | 无 | 客户端，无服务端端口 |

- 所有服务**必须支持环境变量覆盖端口**（如 `GATEWAY_PORT` / `--port`）。
- 实施启动后，把 8772 追加进 `PORTS.md` 的「变更记录」，避免未来与本机其他作业撞端口。

---

## 7. 实施步骤（历史规划，2026-11-14 版）

> ⚠️ 本节是**商业化路线**的历史规划。2026-11-15 起产品改为完全免费开源，其中「定价 / 订阅 / 企业版 / GTM / 试点转化」相关步骤**已作废**；技术类步骤（客户端策略、网关审计、控制台、Docker 打包）已按现状完成，见 §11 与 §12。

**Phase 0 · 立项与命名（1 周）**：定产品名与仓库边界（二合一或明确接口）、端口登记、CI、许可策略（AGPL 开源内核 + 商业许可）、安全白皮书框架。

**Phase 1 · 产品化 ShellMate 客户端（2 周）**：
- 门控规则**可配置化**（本地策略文件 + 远程策略拉取）；
- 新增**密钥泄露检测**（API Key / Token 正则）；
- 团队模式：网关鉴权 + 遥测（默认关、可匿名化）；
- 跨平台打包（pip / brew / 安装包）。保留「本地优先 + 云端兜底」（**已打通**）。

**Phase 2 · 演进 margin-console 为网关（2–3 周）**：
- `raw` 端点**加鉴权**（团队 token）；
- 每次调用**审计落库**；
- **成本估算 / 节省报告**；
- **策略下发端点**（网关 → 客户端门控规则）；
- 限流 / 请求体积限制 / 防滥用。

**Phase 3 · 团队控制台（2 周）**：React 前端改版（登录/团队、用量图、成本节省、策略编辑、审计查看、接入向导），对接网关 API。

**Phase 4 · 商业化与打包（2 周）**：Docker Compose 一键部署（单端口 8772）、定价页、许可、文档（安全白皮书 + 合规说明）、落地页与 Demo。

**Phase 5 · 试点与 GTM（持续）**：招募 5–10 个设计伙伴团队，收集反馈迭代；内容营销（本地优先 / 成本基准 / 防事故案例）。

---

## 8. 关键风险与对策

| 风险 | 对策 |
| --- | --- |
| 免费模型质量偏弱 | 多候选链 + 行协议不合规自动换模型 + 基于实测的质量评测（**已落地**） |
| 隐私承诺 vs 云端兜底 | 必须显式 opt-in + 明确「数据离开本机」警告（**已具备**） |
| 免费模型政策突变 / 过载 | 人工一键停用 + 5 分钟自动熔断冷却（**已落地**） |
| 公开部署被滥用 | 可选 `SAFESHELL_API_TOKEN` 共享令牌（默认关，保持零配置；**已落地**） |
| 无 SLA / 无官方托管 | 明确写入文档：社区自部署、自行承担厂商额度合规，不承诺可用性 |

---

## 9. 首批 MVP（最小可用）

ShellMate CLI（团队模式连网关）+ 网关（免费额度路由 + 审计）+ 基础控制台（用量 + 审计），**Docker Compose 单端口 8772 一键起**。即可演示「自然语言 → 安全命令 → 近零成本 → 可审计」完整闭环。

---

## 10. 现状与后续（2026-11-15 更新）

产品名 **SafeShell Pro**、端口 **8772** 已定，许可已确定为 **AGPL-3.0 全量开源、完全免费**。MVP 已闭环，不再有「商业化下一阶段」。

后续按 `SafeShellPro-评估与改进路线.md` 的工程改进推进（质量评测、延迟优化、审计可信、WSL 稳定性）；**P0-2（Redis 多副本状态外置）与全部定价/GTM 项在免费自部署定位下不再需要**，见 §12。

---

## 11. 实施进度（截至 2026-11-14，已落地）

下方 Phase 已在 `release/mc_src`（网关）与 `ShellMate-V1.0-交接包`（客户端）中实装并端到端验证：

- **Phase 0 命名/端口**：产品名 **SafeShell Pro**；开发网关 + 控制台端口 = **8772**（已登记 `ai-fullstack-delivery/PORTS.md`）。
- **网关审计落库**：`server/db.py` 新增 `raw_audit` 表，raw 端点每次调用落明细（客户端 / 模型 / 模式 / token / 延迟 / 成败 / 节省额）；`GET /api/v1/audit/logs` 可回溯。
- **成本/节省报告**：`server/cost.py` 估算免费档相对付费等价模型的节省额（实付恒为 0）；`GET /api/v1/audit/report` 按客户端/模型/模式聚合。
- **按客户端限流**：`server/rate_limit.py` 进程内令牌桶（默认 30 RPM，对齐 GLM 免费档），`RAW_RPM_PER_CLIENT` 可配；触发返回 429。
- **团队策略下发**：`server/db.py` 新增 `team_policy` 表；`GET/PUT /api/v1/policy` 管理禁用命令正则、风险等级、模型白名单、最大 token、强制本地门控。
- **Docker 单端口打包**：`Dockerfile` 多阶段（前端构建进镜像），`docker-compose.yml` 端口 8772 + healthcheck；`.env.example` 端口/CORS 对齐。
- **前端团队控制台 + 落地页**：新增 `TeamConsole`（用量/成本/审计/策略编辑器）与 `Landing`（产品介绍 + Open-Core 三档定价）；品牌更新为 SafeShell Pro，`web/dist` 已重建。
- **真实模型接入**：已接 **6 个实测免费模型**——智谱 `glm-4-flash` / `glm-4.7-flash`，硅基流动 `Qwen/Qwen3-8B` / `Qwen/Qwen2.5-7B-Instruct` / `THUDM/GLM-4-9B-0414` / `deepseek-ai/DeepSeek-R1-0528-Qwen3-8B`；两把 Key 存于本机 `data/margin.db`（gitignore，不入库）。
- **多模型路由 + 停用**：`connect.resolve_credential(model)` 按模型名路由凭据；`model_status` 表 + `GET /api/v1/models` + `PUT /api/v1/models/status` 实现「政策变化不再免费 → 一键停用」；停用后网关 `403 / MODEL_DISABLED` 硬拒绝，客户端自动从候选链移除。前端团队控制台提供模型启停卡片。
- **客户端策略强制执行**：ShellMate 新增 `src/shellmate/policy.py`（拉策略 + 模型清单，失败静默降级，只收紧不放宽）+ 候选模型链（停用 / 429 / 5xx / 行协议不合规自动换模型），在 `apply_gate()` 之后叠加禁用正则与风险阈值，DENY 清 `command`。
- **超时适配**：`RAW_UPSTREAM_TIMEOUT_S` 默认 60s，覆盖 GLM-4-9B-0414 冷启动 38–44s。

**模型健康度熔断**：`model_health` 表 + 5 分钟自动冷却；上游超时/429/5xx 后网关毫秒级快速拒绝，客户端立即切下一个候选，解决「免费档持续过载导致每次白等 60s」的可用性问题。实测端到端 **66s → 5.2s**。与人工停用互补：临时过载自动熔断、政策变化人工停用。

**Docker 实构（WSL，Docker 29.8.2）**：镜像 `mc_src-app:latest` 已构建成功并跑通。发现并修复两处环境问题：① WSL2 原生 dockerd 的 bridge 出站 DNS 不通 → 构建期 `build.network: host`、运行期 `SFP_NETWORK_MODE=host`；② 新增 `.dockerignore` 排除含真实 Key 的 `data/` 与 `venv/`、`node_modules/`，镜像内零凭据。容器内 health / 前端托管 / 11 项模型清单 / GLM + SiliconFlow 真实转发 / 审计落库 / ShellMate 全链路均实测 OK。

**验证结果**：网关 **64 passed**（含多模型路由、停用、熔断测试）；ShellMate **63 passed**（含 14 项多候选/策略测试）；前端 `npm run build` 通过；6 个模型真实转发、停用→403→启用、团队策略下发后客户端本地 DENY 且命令不输出、ShellMate 全链路（`glm-4.7-flash` 过载 → 自动切 `glm-4-flash` → 门控通过）均实测 OK。

**成本口径澄清**：报告里 `estimated_saved_cny`（如 ¥0.0012）是**对照估算，不是账单**——表示「若这次调用改走付费等价模型大约要花多少」；免费模型实付恒为 `billed_cny = 0`，节省额 = 付费等价 − 0。

**仍待办（商业化口径，2026-11-15 起已作废）**：多实例限流与熔断状态共享、定价与计费口径、登录鉴权与多租户隔离。免费自部署定位下，多副本 Redis 与多租户不再需要；轻量共享令牌鉴权已作为「公开部署可选加固」落地（见 §12）。

---

## 12. 2026-11-15 变更：免费开源 + 改进路线落地

**用户四项决定** → ① 停用 `glm-4.7-flash`；② 产品不收费，给大家免费用；③ 保持常驻 WSL 终端窗口；④ 按改进路线放手实施。

### 12.1 免费开源
- `web/src/components/Landing.tsx` 三档定价页改为「完全免费 · 没有付费版」三张说明卡；`TeamConsole` 标题改为「全部功能免费开放」；`release/mc_src/LICENSE` 加入 AGPL-3.0 全文。
- 不设官方托管、不设 SLA、不设付费支持；用户自带各厂商免费 API Key、自部署。

### 12.2 停用 glm-4.7-flash
- `server/db.py` 新增 `DEFAULT_DISABLED_MODELS`（含停用原因）；无 `model_status` 记录时默认停用，管理员可在控制台显式「启用」覆盖。清单展示 `reason`。
- ShellMate 默认候选链（light / deep）与 `cloud_model_deep` 均移除该模型，当前 **5 个启用模型**。

### 12.3 WSL 常驻
- 新增 `keepalive.sh` + `keep-wsl.bat`（双击后最小化常驻），`install.sh keepalive | keepalive-once | keepalive-stop`。
- 常驻会话守护 dockerd + 容器 + health（30s 一轮），消除 WSL 休眠导致的容器重启与 Windows 侧间歇 502。

### 12.4 改进路线已落地项
| 编号 | 内容 | 落点 |
| --- | --- | --- |
| P0-3 | 审计哈希链 + `/audit/verify` + `/audit/export`（jsonl/csv），只存命令 SHA-256 不存原文；旧行标记 `legacy_rows` 不误报 | `db.py` / `main.py` / `models.py` / `TeamConsole` |
| P0-1 轻量版 | 可选 `SAFESHELL_API_TOKEN`（默认空=零配置），客户端 `SHELLMATE_CLOUD_TOKEN`，前端控制台可填令牌 | `config.py` / `main.py` / `client.py` / `api.ts` |
| P1-1 | `/overview`、`/models` 3s TTL 缓存；写操作自动失效；按模型分级超时；可选启动预热；按审计实测延迟/成功率对候选链稳定降权 | `cache.py` / `raw.py` / `db.model_latency_stats` / `policy.py` |
| P1-2 | `eval/cloud_eval.py` 云端真实模型质量评测（可用率 / 行协议合规率 / 风险一致率 / p50·p95 / 失败原因），产出 `baseline_cloud.json` | ShellMate `eval/` |
| P1-3 | `/api/v1/metrics` Prometheus 文本指标（调用量、错误、延迟、按模型成功率、熔断数） | `main.py` |
| P1-4 | `keepalive` 子命令 + `.bat` 常驻窗口 + `doctor` 提示 | `install.sh` / `keep-wsl.bat` |

**明确不做（并说明理由）**：P0-2 Redis 多副本状态外置——免费自部署是单实例，多副本一致性属 SaaS 场景；完整多租户/SSO——本地部署下 `X-Client-ID` 仅用于归因，公开部署用轻量令牌即可；P2 全部定价/GTM/企业版——与「不收费」决策冲突。

### 12.5 2026-11-15 后续：开源上线、网络修复、WSL 迁移

- **GitHub 第一版**：新建公开仓库 [`lettroi/safeshell-pro`](https://github.com/lettroi/safeshell-pro)，128 文件 / 2.23MB，上传前做了两轮密钥审计（`githubtoken.txt`、`*.db`、`.env`、venv、node_modules、zip、`.workbuddy/` 全部排除，远端复核为 0 命中）。因 Windows Git 的 Schannel 吊销检查 + `github.com` 被 hosts 劫持，常规 `git push` 不可用，改用 GitHub Git Data API 建 blob/tree/commit/ref。
- **dockerd 网络修复**：根因是 WSL 自启脚本以 `dockerd --iptables=false --ip6tables=false` 启动，缺 MASQUERADE（bridge 出站）与 DNAT（端口映射）。已切 `iptables-legacy`、去掉禁用参数、重启 dockerd；`SFP_NETWORK_MODE` 回到标准 **bridge**，Windows→8772→真实模型全链路 200。新增 `install.sh fix-network` 与 `keepalive.sh` 的幂等兜底；`install.sh` 不再自动退化到 host。
- **代码迁移**：主工作区迁至 **`/root/safeshell-pro`（WSL2 ext4）**，Windows 侧原目录转只读归档；`data/margin.db`（含真实 Key）与 `.env` 随迁、脚本可执行位修正；容器改由 WSL 目录的 compose 启动。Windows 侧 `.bat` 降级为转发器（legacy）。
- **第二版提交**：迁移后的路径/网络文档、脚本与台账变更推送到同一仓库，形成「迁移前 / 迁移后」两版可对比历史。
- 仍需常驻 WSL 会话（实例休眠属 VM 生命周期，与代码位置、网络模式无关）。
