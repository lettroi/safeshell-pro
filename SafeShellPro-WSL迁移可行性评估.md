# SafeShell Pro：完全迁移到 WSL 的可行性评估

> 评估日期：2026-11-15 ｜ 性质：**只读调研 + 影子副本验证，未改动任何现有代码、配置、容器、数据或服务**
> 用户诉求：「一半 Windows、一半 Docker 有点奇怪」，想知道能否统一到 WSL。先不改。

---

## 0. 结论（TL;DR）

**可行，且推荐迁移；但「完全」有明确边界。**

- 建议把**开发、构建、运行、数据**全部落到 WSL2 的 ext4（例如 `/root/safeshell-pro`），Windows 退化为**纯客户端**（浏览器 + 文件管理 + 文档）。这一步技术上没有阻塞：源码零 Windows 路径硬编码、脚本全 LF、影子副本在 ext4 上 `docker compose config -q` 与语法检查均通过。
- 收益很实：跨边界小文件 I/O 快 **约 69 倍**、SQLite 从 9p 网络文件系统搬回本地 ext4（**数据完整性**提升）、中文路径与 `WSLENV /wslpath` 转换整套消失、CLI 环境探测从 `Windows/powershell` 变成 `Ubuntu/bash`（语义终于对上生成的 Linux 命令）、C 盘 30G 压力转移到 ext4 的 234G。
- **但有三件事迁移解决不了**，必须提前说清，否则期望会落空：
  1. **bridge 出站整体不通**（实测连公网 IP 的 TCP 都超时，不只是 DNS）→ 仍需 `host` 网络，或换 Docker Desktop / 修 dockerd 网络。
  2. **WSL 实例休眠**：本机是 in-box 旧版 WSL（`wsl --version` 报「命令行选项无效」），`vmIdleTimeout` 实验配置大概率不可用 → 常驻会话 / `keepalive.sh` 仍需要。
  3. **Windows 侧资产无法消失**：浏览器、`APIKEY/`、端口台账 `PORTS.md`、交付文档、release 归档包、WorkBuddy 编辑器都在 Windows，只能经 `/mnt/c` 只读互访。
- 因此准确的定位是：**从「混合双栈」变成「WSL 单栈 + Windows 瘦客户端」**，而不是「彻底不用 Windows」。

---

## 1. 现状：别扭感从哪来

```
Windows 侧 (C:\...\软件开发\)
  ├─ release/mc_src/            源码、venv(48M)、node_modules(64M)、data/margin.db(含真实Key)
  ├─ ShellMate-V1.0-交接包/     CLI 在这里用 Windows Python 跑
  ├─ install.bat / keep-wsl.bat 靠 WSLENV /p + wslpath 转换中文路径
  ├─ APIKEY/ · PORTS.md · 文档 · release zip
  └─ 浏览器访问 127.0.0.1:8772 ──(localhostForwarding)──┐
                                                        │
WSL2 Ubuntu 24.04 (ext4) ───────────────────────────────┤
  ├─ dockerd（原生，systemd=offline，靠 /etc/profile.d login shell 拉起）
  ├─ 容器 safeshell-pro-app（host 网络，监听 8772）
  └─ 卷 ./data、./logs 实际是 /mnt/c/...（9p drvfs）─────┘
```

**四个结构性摩擦**：

| # | 摩擦 | 根因 |
|---|---|---|
| 1 | 路径跨边界 + 中文目录 | 源码在 `C:\Users\...\软件开发\`，容器只认 `/mnt/c/...`；`install.bat` 必须 `WSLENV=SFP_WIN_DIR/p` + `wslpath` + login shell |
| 2 | I/O 慢、构建慢 | `/mnt/c` 是 **9p drvfs，带 dirsync**，大量小文件极慢（见 §2） |
| 3 | WSL 会话一断就 502 | 最后一个 WSL 会话退出 → 实例休眠 → host 网络容器重启；本机无 systemd、dockerd 靠 login shell 自启 |
| 4 | 语义错配 | CLI 在 Windows 跑，`detect()` 返回 `distro=Windows / shell=powershell`，却生成 Linux 命令 |

---

## 2. 实测证据（2026-11-15，本机）

| 项目 | 实测 | 含义 |
|---|---|---|
| `/mnt/c` 挂载 | `9p (rw,noatime,dirsync,...,msize=65536,trans=fd)` | 网络文件系统语义，非本地块设备 |
| WSL `/` | `ext4 (rw,relatime,discard)`，**234G 可用** | 空间与 I/O 都充裕 |
| Windows C: | 112G，**已用 83G / 仅剩 30G（74%）** | 继续在 C 盘放 venv+node_modules 不划算 |
| 300 个小文件写 | ext4 **27ms** vs drvfs **1860ms** → **约 69×** | `npm ci` / `pip install` 会创建上万文件，差距被放大 |
| 300 个小文件读 | ext4 248ms vs drvfs 1840ms → 7.4× | 前端构建、pytest 收集也受影响 |
| SQLite 位置 | `data/margin.db` 在 `/mnt/c`，`journal_mode=delete` | **SQLite 文件锁跑在 9p 上，官方不推荐网络文件系统**；审计哈希链/凭据库都在这里，属数据完整性隐患 |
| WSL 版本 | `wsl --version` →「命令行选项无效」＝ **in-box 旧版** | Store 版才支持 `[experimental] vmIdleTimeout`；本机大概率无法用配置免休眠 |
| systemd | `offline` | dockerd 靠 `/etc/profile.d/docker-auto.sh`，只有 login shell 触发 → `bash -lc` 是硬要求 |
| bridge 出站 | DNS `FAIL`；直连 `223.5.5.5:443` / `8.8.8.8:53` / `114.114.114.114:53` **全 TimeoutError** | **不只是 DNS，是 bridge 出站整体不通**；`daemon.json` 只有 registry-mirrors，加 dns 也修不好（此前已实测）。与代码放哪无关 |
| 容器 | `NetMode=host`，`Restart=unless-stopped`，8772 已发布但 host 模式忽略 | 迁移后仍需 host 网络 |
| 源码规模 | 排除 venv/node_modules/dist 后 **161 文件 / 3.5MB**（影子副本 160 / 2.9M） | 迁移体量极小 |
| 换行符 | `install.sh` / `keepalive.sh` / `Dockerfile` / `compose` / `.bat` 全 **LF-only，CRLF=0** | 迁移无换行符坑 |
| Windows 硬编码 | `server/`、`web/src/`、`compose`、`Dockerfile` 内 **零** `C:\` / `C:/Users` 硬编码 | 代码层可直接搬 |
| 影子副本验证 | 复制到 `/root/_migrate_probe` 后 `docker compose config -q` **OK**、`compileall` **OK**、shebang 正常 | **可行性已实证**（副本已删除） |
| WSL 工具链 | python3 **3.12.3**、node **v22.23.2**、npm 10.9.8、git 2.43、curl 8.5；sqlite3 CLI 缺失 | Docker 构建自带 3.13.14，不依赖宿主；若要 WSL 本地 venv 需另装 3.13 |
| CLI 环境探测 | `distro=Windows, shell=powershell` | 迁到 WSL 后变 Ubuntu/bash，与生成的 Linux 命令一致 |
| 端口 | Windows→8772 health **200**，`localhostForwarding=true`（`.wslconfig`） | 迁移后入口不变 |

---

## 3. 迁移能解决 / 不能解决

### ✅ 能解决
- **I/O 与构建**：源码、node_modules、venv、镜像构建上下文全在 ext4；`npm ci` / `pip install` 不再跨 9p。
- **SQLite 可靠性**：`margin.db`（真实 Key、审计哈希链、模型状态、策略）回到本地 ext4，锁与 WAL 语义正常。
- **路径复杂度**：中文路径、`WSLENV /p`、`wslpath`、`.bat` 转发器整套可退场；主入口变成 `bash install.sh`。
- **CLI 语义**：`envinfo.detect()` 变成 Ubuntu/bash，命令建议与本机一致。
- **磁盘**：C 盘 30G 压力转移到 ext4 234G。
- **脚本简化**：`install.bat` 可降为一行转发器或删除；`need_docker` 里为 WSL 唤醒加的重试逻辑可保留但不再是核心。

### ❌ 不能解决（重要）
| 问题 | 为什么迁不动 | 真正解法 |
|---|---|---|
| bridge 出站不通 | 实测是 bridge NAT/转发层面不通，与项目路径无关 | 继续 `SFP_NETWORK_MODE=host`；或换 Docker Desktop；或重建 dockerd 网络 |
| WSL 休眠 / 502 | 实例休眠是 WSL VM 生命周期，代码位置无关 | 常驻会话（`keep-wsl.bat` / `keepalive.sh`）；或升级 Store 版 WSL 试 `vmIdleTimeout`；或 Docker Desktop |
| Windows 浏览器访问 | 仍需 `localhostForwarding` | 已开启且实测 200，不受影响；无需改 |
| Windows 侧资产 | 桌面、`APIKEY/`、`PORTS.md`、文档、release zip、编辑器都在 Windows | 保留在 Windows，WSL 经 `/mnt/c` 只读互访 |
| `package_release.py` / 免安装运行包 zip | 脚本注明「仅保证当前这台 Windows 机器可用（venv 绑定本机 Python 路径）」 | 作为历史归档留在 Windows；新版分发改 WSL 内打包或 PyPI |

---

## 4. 三个方案对比

| | **A. 全量迁到 WSL ext4（推荐）** | **B. 现状减负** | **C. 换 Docker Desktop** |
|---|---|---|---|
| 做法 | 源码/`.env`/`data`/构建/容器/CLI 全放 `~/safeshell-pro`；Windows 只留浏览器+文档+Key台账 | 源码仍在 C 盘，只把 `data` 卷移进 ext4、删掉 venv/node_modules | 用 Docker Desktop 的 bridge + ports，Windows 侧跑容器 |
| 解决 I/O / SQLite | ✅ 全部 | ⚠️ 只解决 DB | ✅（容器卷在 WSL VM 内） |
| 去掉中文路径 / .bat | ✅ | ❌ | ⚠️ 仍需路径转换 |
| 去掉 host 网络 hack | ❌ 仍需 | ❌ 仍需 | ✅ |
| 去掉常驻窗口 | ❌ 仍需 | ❌ 仍需 | ✅（Docker Desktop 常驻） |
| 改动量 | 中（一次性拷贝 + 路径/文档更新） | 小 | 大（换运行时，重装/迁移镜像与卷） |
| 附加成本 | 需一次性迁移 Key 与数据、双轨验证 | 根因未除，别扭感仍在 | Docker Desktop 许可/资源占用、与现有 WSL 原生 dockerd 二选一 |
| 与「免费开源」定位 | 一致 | 一致 | 一致（但引入商业桌面软件） |

**推荐 A**：它把用户最反感的「一半一半」消掉，且不引入新依赖；C 虽能顺带解决 host/休眠两大痛点，但等于换一整套运行时，属于另一个决策，建议单独评估。

---

## 5. 方案 A 的实施步骤（**本轮不执行**，供拍板后用）

> 端口遵守既有约定：迁移期间如需并行验证，用 **8773**（避开 8772 与预留段），并先查 `ai-fullstack-delivery/PORTS.md` + 实时探测；正式切换回 **8772**。

**阶段 0 · 备份与冻结**
1. 停写：`docker compose stop`（保留容器与卷），导出 `data/margin.db` 到 Windows 侧加密备份（含 3 条真实凭据）。
2. 记录当前 `/api/v1/audit/verify` 结果、`/models` 启用状态、`team_policy` 作为迁移后比对基线。
3. 确认回退路径：旧目录 + 旧 `.env` 原样可起。

**阶段 1 · 建 ext4 工作区**
1. `mkdir -p /root/safeshell-pro`；用 `tar` 从 `/mnt/c/.../mc_src` 复制源码，**排除** `venv/`、`web/node_modules/`、`logs/`、`*.db`。
2. 单独复制 `data/margin.db`，`chmod 600`；`cp .env.example .env` 后把本机差异（`SFP_NETWORK_MODE=host`、`RAW_MODEL_TIMEOUTS` 等）迁移过去，**真实 Key 只在 DB 里，不写 `.env`**。
3. 校验：LF、`#!/usr/bin/env bash`、`docker compose config -q`、路径无空格/中文。
4. 顺手 `git init`（当前 `release/mc_src` **不是 git 仓库**，迁移是补版本控制的好时机），确认 `.gitignore` 覆盖 `data/*.db`、`.env`、`venv/`、`node_modules/`。

**阶段 2 · 构建与数据一致性**
1. `docker compose build`（ext4 上应明显快于 `/mnt/c`；构建期 `network: host` 保留）。
2. 临时用 8773 起新容器，比对：凭据数、`/models` 清单与停用项、`team_policy`、审计哈希链 `ok=true`、导出 CSV 行数。
3. 跑双端测试（WSL 内建 Python 3.13 venv 或直接容器内跑）与前端构建。

**阶段 3 · 切换**
1. 停旧容器 → 新容器改回 8772 → `docker compose up -d`。
2. 验收：health、前端、`/models`、真实模型调用（≥2 个模型）、`/audit/verify`、**ShellMate CLI 从 WSL 内跑**（`envinfo` 应显示 Ubuntu/bash）、Windows 浏览器 `127.0.0.1:8772`。
3. `PORTS.md` 追加一条「占用方路径由 `C:\...` 变为 WSL `/root/safeshell-pro`，端口仍 8772」的变更记录。

**阶段 4 · 收尾**
1. Windows 侧原目录转为**只读归档**（保留一段时间作回退），删除 `venv/` 与 `web/node_modules/` 释放 C 盘。
2. 更新 README / 用户手册 / CHANGELOG / HANDOFF / SKILL 的路径与入口（`install.bat` 降为转发器或标注 legacy）。
3. `keep-wsl.bat` / `keepalive.sh` 保留（休眠问题仍在），把路径改到新工作区。
4. 工作区记忆（`MEMORY.md`、日志）记录新根目录。

**验收标准**：双端 pytest 全绿、前端 build 通过、容器内 5 个启用模型真实转发、哈希链校验通过、CLI 全链路 EXIT=0、Windows 侧 health 200、C 盘释放 ≥100M、旧目录可一键回退。

**回退**：停新容器 → 旧目录 `docker compose up -d` → health 200，数据与 Key 均未丢失（阶段 0 备份兜底）。

---

## 6. 风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| 真实 Key / 审计数据在复制中丢失或泄露 | 高 | 停写后复制、`chmod 600`、备份加密、复制完校验凭据数与哈希链；临时副本用完即删 |
| 9p 上 SQLite 已存在隐性损坏 | 中 | 迁移前 `PRAGMA integrity_check`；异常则从备份重建 |
| 切到 ext4 后仍误以为网络问题已修 | 中 | 明确记录：bridge 出站不通与路径无关，`.env` 必须保留 `SFP_NETWORK_MODE=host` |
| WSL 休眠导致新容器仍 502 | 中 | 保留常驻窗口；或单独评估 Docker Desktop / Store 版 WSL |
| 文档、脚本、记忆里大量旧路径失效 | 中 | 阶段 4 全量 grep `C:\...`、`软件开发`、`mc_src` 并替换；旧文档标注归档 |
| `package_release.py` 与 Windows 免安装包作废 | 低 | 作为历史归档保留；新分发走 WSL 内打包 / PyPI |
| WSL 内 python 3.12 ≠ 项目 3.13.14 | 低 | 只走 Docker 构建则无关；若要本地 venv 用 `uv python install 3.13` 或容器内跑测试 |
| 端口 8772 迁移期冲突 | 低 | 并行验证用 8773，切换回 8772；台账同步 |

---

## 7. 需要拍板的四件事

1. **Windows 侧只留「浏览器 + 文档 + APIKEY + PORTS.md」**能否接受？（这是「完全迁移」的现实含义）
2. 是否**顺带初始化 git**（当前无版本控制，迁移是低成本补齐时机）？
3. `install.bat` / `keep-wsl.bat` 是**保留为转发器**还是标记 legacy 只留 `*.sh`？
4. 是否要**单独评估 Docker Desktop**（能一举解决 host 网络 + 休眠，但等于换运行时）？

> 在拍板前，现有服务、代码、配置、容器与数据**保持原样**：`127.0.0.1:8772` health 200，保活会话仍在运行。
