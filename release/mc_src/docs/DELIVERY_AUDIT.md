# AI 全栈交付审计报告

- 项目：`C:\Users\Administrator\Desktop\margin-console\fullstack`
- 时间：2026-11-13 15:21:11
- 范围：全部五阶段
- 结果：通过 31 项 / 警告 0 项 / **失败 0 项**（严格通过率 100%）

> 本脚本只检查形式合规。内容质量（指标口径、原型是否真的三态、Bad Case 归因是否到位）须按 `references/06-pitfalls-and-gates.md` 人工复核。

## S1 产品定义与原型  [5/5]

- [PASS] `S1-01` PRD 文档存在 — 找到 PRD.md
- [PASS] `S1-02` PRD 五类关键字段齐全 — Persona / Pain Point / 用户故事 / MVP / NFR 均可检出
- [PASS] `S1-03` NFR 含可测数值 — 检出百分比 / p95 / TTFT / token 等量化口径
- [PASS] `S1-04` 三态原型截图齐全 — docs/prototypes/ 命中：normal, loading, error
- [PASS] `S1-05` README 引用原型截图 — README 已引用原型

## S2 工程环境与基础搭建  [6/6]

- [PASS] `S2-01` 依赖清单存在 — 找到 requirements.txt
- [PASS] `S2-02` 依赖全部锁定精确版本 — 6 条依赖均带 == 精确版本
- [PASS] `S2-03` .env.example 存在且无真实密钥 — 找到 .env.example，占位值合规
- [PASS] `S2-04` .gitignore 覆盖密钥与缓存目录 — 已忽略 .env / venv / __pycache__
- [PASS] `S2-05` .env 未被 git 跟踪 — git ls-files 未发现 .env
- [PASS] `S2-06` Python 版本已固定 — 检出 .python-version / requires-python / 固定镜像 tag

## S3 后端开发与 Agent  [7/7]

- [PASS] `S3-01` FastAPI 入口与端点存在 — 检出 FastAPI( 与路由装饰器
- [PASS] `S3-02` Pydantic 请求/响应模型存在 — 检出 BaseModel 子类与 response_model
- [PASS] `S3-03` LLM 参数显式设置 — 检出 timeout 与 temperature
- [PASS] `S3-04` 检索为空短路 — 检出空结果短路分支
- [PASS] `S3-05` 异常兜底与 error_code — 检出分类错误码
- [PASS] `S3-06` 结构化日志含 request_id 与 token_usage — 检出 request_id 与 token_usage
- [PASS] `S3-07` Prompt 版本化与引用溯源 — 检出 prompt_version 与 sources

## S4 测试与质量保障  [8/8]

- [PASS] `S4-01` 黄金评测集存在且合法 — tests\eval_dataset.jsonl 共 24 条，全部为合法 JSON
- [PASS] `S4-02` 评测集不少于 10 条 — 24 条
- [PASS] `S4-03` 每行含必需字段 — id / input / expected_type 齐全
- [PASS] `S4-04` 四层覆盖齐全 — functional / boundary / safety / robustness 全覆盖
- [PASS] `S4-05` 含 chitchat / refusal / no_context 用例 — 三类关键意图均有用例
- [PASS] `S4-06` pytest 测试文件存在 — 找到 2 个 test_*.py
- [PASS] `S4-07` 评测报告含实测数值与口径 — 检出百分比与延迟指标
- [PASS] `S4-08` Bad Case 记录存在 — 找到 docs\BAD_CASES.md

## S5 交付与运维文档  [5/5]

- [PASS] `S5-01` README 含一键启动命令 — 检出启动命令
- [PASS] `S5-02` README 含环境变量说明 — 检出环境变量说明
- [PASS] `S5-03` CHANGELOG 含语义化版本号 — 检出 x.y.z 版本号
- [PASS] `S5-04` 日志字段齐全（代码侧） — 检出 prompt_version / model / latency
- [PASS] `S5-05` 容器化或部署配置存在 — 找到 docker-compose.yml

---

交付判定：**无 FAIL 项**才可宣称「已交付」；WARN 项须按 references 人工确认，未达标项须在 EVAL_REPORT「已知局限性」逐条登记。
