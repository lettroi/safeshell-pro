# SafeShell Pro

**完全免费 · AGPL-3.0 全量开源 · 无付费版 · 无官方托管**

把自然语言变成**经过安全门控**的运维命令：本地优先、危险命令硬拦截、推理走免费大模型（实付 ¥0）、全程可审计。

由两个项目合并而成：

| 组件 | 目录 | 作用 |
|---|---|---|
| **ShellMate CLI** | `ShellMate-V1.0-交接包/` | 零第三方依赖的本地命令行助手。NL2Cmd（自然语言→命令）、Cmd2NL（命令→白话）、参数级静态风险门控（`DENY` 时清空命令）、本地优先 / 云端兜底 |
| **网关 + 团队控制台** | `release/mc_src/` | FastAPI + React/TS。免费模型额度路由、耗尽/过载自动切换、按客户端限流、审计哈希链、团队策略下发、模型启停、Docker 单端口部署 |

三条铁律：**只生成 · 不执行 · 不隔离**；**命令缓冲 → 校验 → 整行呈现**；**反馈只存本机 · 默认不联网**。

---

## 快速开始

**主工作区：`/root/safeshell-pro`（WSL2 ext4，2026-11-15 迁移完成）**。源码、数据、构建与运行都在 WSL 内；Windows 侧原目录已转为只读归档。

```bash
cd /root/safeshell-pro/release/mc_src
bash install.sh              # 检查环境 → 构建 → 启动 → 健康检查
bash install.sh status       # 容器状态
bash install.sh doctor       # 诊断 Docker / 端口 / 网络模式 / NAT
bash install.sh fix-network  # WSL dockerd 的 bridge NAT 兜底修复（幂等）

# 浏览器打开 http://127.0.0.1:8772   （8000 是本机禁用端口，勿用）
```

- `install.bat` / `keep-wsl.bat` 仍保留，但只是 **Windows 转发器（legacy）**：真正的主入口是 WSL 里的 `bash install.sh`。
- WSL2 需要**保持一个常驻终端窗口**：`bash keepalive.sh`（或双击 `keep-wsl.bat` 后最小化），否则实例休眠会重启容器、Windows 侧出现间歇 502。
- 网络统一用 **bridge**。若容器出不了网，先执行 `bash install.sh fix-network`（根因通常是 dockerd 以 `--iptables=false` 启动，缺 MASQUERADE / DNAT），**不要**直接退化到 host 模式。

首次安装不需要任何密钥，默认演示模式可体验全部界面；按页内向导填入自己的免费 API Key 后切换为真实模型。

**完整安装、使用、运维、排障、API 与安全边界**见 [`SafeShellPro-User-Manual.md`](SafeShellPro-User-Manual.md)。

## 当前接入的免费模型

默认启用 5 个（跨两家平台，同一 `/api/v1/raw/completions` 按 `model` 字段路由）：

| 平台 | 模型 | 说明 |
|---|---|---|
| 智谱 BigModel | `glm-4-flash` | 轻量快、30 并发，当前首选 |
| 硅基流动 | `Qwen/Qwen2.5-7B-Instruct` | 快，行协议偶有不合规，客户端自动换模型 |
| 硅基流动 | `Qwen/Qwen3-8B` | 通用，行协议遵循度较好 |
| 硅基流动 | `THUDM/GLM-4-9B-0414` | 编程强，冷启动较慢，兜底 |
| 硅基流动 | `deepseek-ai/DeepSeek-R1-0528-Qwen3-8B` | 推理型，慢且啰嗦，仅作兜底 |

`glm-4.7-flash` 因智谱免费档长期 429 / 超时，自 2026-11-15 起**默认停用**（保留预设，管理员可在控制台启用）。免费政策变化时，在「团队控制台 → 免费模型清单」一键停用即可：网关硬拒绝 + 客户端自动从候选链移除。

## 关键能力

- **安全门控**：`gate.py` 参数级规则引擎，高危命令本地硬拦截；团队策略（禁用正则 / 风险阈值 / 模型白名单）在 `apply_gate()` 之后叠加，只收紧不放宽。
- **零成本推理**：多候选模型链，停用 / 429 / 5xx / 行协议不合规自动换下一个；5 分钟自动熔断冷却；按实测延迟与成功率稳定降权。
- **可审计**：每次调用落库，只存命令 SHA-256 不存原文；`prev_hash`/`row_hash` 组成哈希链，`GET /api/v1/audit/verify` 可校验篡改，`/audit/export` 可导出。
- **可运维**：`/api/v1/metrics` Prometheus 指标、TTL 缓存、分级超时、可选共享令牌 `SAFESHELL_API_TOKEN`（默认空=零配置，公网部署前务必设置）。
- **一键部署**：Docker 多阶段构建把前端编译进镜像，单端口 8772 同时托管 API 与页面。

报告里的 `estimated_saved_cny` 只是「若改走付费等价模型大约要花多少」的**对照估算，不是账单**；免费模型实付恒 `billed_cny = 0`。

## 测试

```bash
cd release/mc_src && pytest -q                       # 网关 77 passed
cd ShellMate-V1.0-交接包/shellmate && python -m pytest tests/ -q   # CLI 69 passed
cd release/mc_src/web && npm run build
```

真实模型质量 baseline 见 `ShellMate-V1.0-交接包/shellmate/eval/baseline_cloud.json`（`eval/cloud_eval.py` 生成）。

## 文档索引

| 文档 | 内容 |
|---|---|
| `SafeShellPro-User-Manual.md` | 安装 / 使用 / 运维 / 排障 / API / 安全边界 |
| `SafeShellPro-评估与改进路线.md` | 实测性能、适用群体、Docker 论证、质量 baseline、改进路线与当前状态 |
| `SafeShellPro-商业项目方案.md` | 技术方案与 2026-11-15 免费开源定位变更（历史商业化规划已作废） |
| `SafeShellPro-WSL迁移可行性评估.md` | 迁移到 WSL ext4 的可行性、实测数据、步骤与风险 |
| `release/mc_src/README.md` | 网关详细文档与 API 表 |
| `ShellMate-V1.0-交接包/HANDOFF.md` · `SKILL.md` | 客户端接手文档 |

## 安全与许可

- **密钥**：真实 API Key 只存服务端本机 `data/margin.db`（已被 `.gitignore` 排除），页面/日志仅脱敏；`.dockerignore` 排除 `data/`，镜像内零凭据。
- **不执行**：所有命令只是文本，是否执行由你决定。
- **许可**：[AGPL-3.0](release/mc_src/LICENSE)。可自用、修改、分发、商用；以网络服务形式提供时须按 AGPL 提供对应源码。
- **无 SLA**：社区自部署，可用性取决于你自己的机器与厂商免费档。
