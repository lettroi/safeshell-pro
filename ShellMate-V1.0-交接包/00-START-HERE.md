# ShellMate V1.0 · 开发交接包

**解压即用。** 这是 2026-11-11 完成的 V1.0 工程、设计依据与后续路线的完整快照，目标是让你在另一台电脑上零猜测地接着开发。

---

## 一、30 秒确认包是好的

在解压出来的目录里打开终端：

```bash
cd shellmate
python --version                                  # 需要 3.10+（开发机实测 3.13.14）
python start.py --mock --no-color --once "怎么查看大于 100M 的文件？"
python -m unittest discover -s tests              # 期望 46 tests OK
python eval/run_eval.py                           # 期望门控分级准确率 100.0%
```

三条都不需要 Ollama、不联网、不装任何第三方包。如果这三条挂了，先别改代码，去看 `NEXT-STEPS.md` 的阶段 0 排错表。

## 二、包里有什么

| 路径 | 作用 |
|---|---|
| `00-START-HERE.md` | 本页，解压后第一眼看的 |
| `HANDOFF.md` | **关键信息**：为什么这么设计、架构、数据契约、不变量、验证状态与未验证项 |
| `NEXT-STEPS.md` | **接下来可以做什么**：分阶段操作步骤，每步含命令、通过标准、失败处理 |
| `SKILL.md` | **项目 Skill 源本**：给 AI 会话加载的压缩上下文与硬约束 |
| `.workbuddy/skills/shellmate-dev/SKILL.md` | 同一份 Skill，已按项目级目录摆好 —— **把本解压目录作为工作区打开就自动生效** |
| `shellmate/` | 完整可运行工程（24 个文件，零第三方依赖） |
| `shellmate/README.md` | 工程自身的部署、用法、安全设计、指标口径、已知限制 |
| `shellmate/DEMO.md` | 本机实跑的 8 屏终端演示输出，可作视觉对照 |
| `design/ShellMate-TUI-交互稿-V1.0.png` | 设计稿整图（8 屏 + 规范条 + 5 步门控链路） |
| `pack.py` | 改完代码后一键重新打包 / 校验完整性 |
| `MANIFEST.sha256` | 全部文件的校验值 |

## 三、三条入口，按需要走

1. **自己接着写代码** → 打开 `NEXT-STEPS.md`，从阶段 0 开始。
2. **让新电脑上的 AI 会话接手** → 把这个解压目录设为工作区（Skill 已在 `.workbuddy/skills/` 就位）；若把 `shellmate/` 单独搬进别的工作区，把 `SKILL.md` 复制到该工作区的 `.workbuddy/skills/shellmate-dev/SKILL.md`，然后直接说「按 Skill 继续开发 ShellMate」。
3. **先搞清来龙去脉** → `HANDOFF.md`，尤其是「不变量」和「验证状态」两节。

## 四、必须知道的一句话

> **真实模型推理在原开发机上没有验证过**（那台机器没装 Ollama）。所有已通过的证据来自内置 `--mock` 样例与离线门控自检。命令可用率、幻觉率、TTFT 必须在装好 `qwen3:8b` 的目标机器上跑 `python eval/run_eval.py --live` 才算数，脚本会把调用失败如实记录，不伪造通过率。

对应的第一步就是 `NEXT-STEPS.md` 阶段 1。别把这当成「已经跑通了」——harness 跑通了，模型还没。
