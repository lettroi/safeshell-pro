# 免费大模型 API 政策清单（静态快照）

> 清单版本：`free-tier-2026-11-13` ｜ 核实日期：**2026-11-13** ｜ 条目：15（其中 9 条来自官方文档 / 官方 PDF 直证）
> 机器可读源：`server/data/free_tier_catalog.json`；检索入口 `GET /api/v1/discover?q=`；代码版本常量 `server/config.py:KNOWLEDGE_VERSION`。

## 这份清单是什么、不是什么

- **是**：调研时点逐条打开厂商官方定价页 / 限流页 / 计费文档后记录的静态快照，每条带可点击的官方来源、核实日期与核实级别。运行时**完全离线**，不发起任何外部请求、不需要密钥。
- **不是**：实时政策接口，也不是「已接入这些模型」。项目的额度表、Usage 轮询、模型回答在 `PROVIDER_MODE=mock` 下仍是演示数据；本清单只负责「政策问答」这一路的知识来源。
- **免费政策变化极快**：2026 年内就有多个免费层被回收。任何「送多少 token」的数字都只是当期快照，**动手前请点来源链接复核**。

## 一、长期免费层（不绑卡，按限流约束）

| 平台 | 代表模型 | 官方公布的关键限额 | 门槛 | 官方来源 | 核实级别 |
| --- | --- | --- | --- | --- | --- |
| Google Gemini（AI Studio） | gemini-3.8-flash、3.6-flash、3.5-flash-lite、3.1-flash-lite、Gemma 4 | 定价页按模型标 `Free of charge`；**官方已不再公布**逐模型 RPM/RPD，需在 AI Studio 项目内查看；限流按项目而非 API Key，RPD 太平洋时间午夜重置 | Google 账号，无需信用卡 | [定价页](https://ai.google.dev/gemini-api/docs/pricing) / [限流页](https://ai.google.dev/gemini-api/docs/rate-limits) | official_docs |
| Groq | openai/gpt-oss-120b、qwen/qwen3.8-27b | **30 RPM / 1,000 RPD / 8K TPM / 200K TPD**；按组织统计；429 响应带 `x-ratelimit-*` 与 `retry-after` | 邮箱注册 | [Rate Limits](https://console.groq.com/docs/rate-limits) | official_docs |
| OpenRouter | ID 以 `:free` 结尾的模型 | **20 RPM**；累计购买 <10 credits **50 次/天**，≥10 credits **1,000 次/天**（实际从 9 credits 起）；`GET /api/v1/key` 返回 `free_model_daily_requests` | 邮箱注册 | [API Limits](https://openrouter.ai/docs/api-reference/limits) | official_docs |
| Cloudflare Workers AI | 平台内置开源模型 | 文本生成 **300 RPM**、语音识别 720 RPM、文本嵌入 3000 RPM、文生图 720 RPM；付费模型 20 RPM | Cloudflare 账号 | [Limits（2026-09-17 更新）](https://developers.cloudflare.com/workers-ai/platform/limits/) | official_docs |
| 智谱 BigModel | GLM-4.7-Flash（200K）、GLM-4-Flash-250414、GLM-Z1-Flash、GLM-4.6V-Flash 等 | 官方定价页输入 / 输出均标「免费」；并发与限流以控制台为准 | 注册账号 | [API 定价](https://docs.bigmodel.cn/cn/guide/start/pricing) | official_docs |
| Cohere | Command A / R 系列 | Trial / evaluation key **1,000 次调用/月**，Chat **20 req/min**；Embed 2,000 inputs/min、Rerank 10 req/min | 注册即可 | [API Keys & Rate Limits](https://docs.cohere.com/docs/rate-limits) | official_docs |
| Mistral La Plateforme | Free mode | 官方仅说明用于测试与原型，**固定限额未公布**，需到 Admin panel 的 Limits 页查看 | 邮箱 + 手机验证 | [官方文档](https://docs.mistral.ai/) / [帮助中心](https://help.mistral.ai/) | official_page_plus_third_party |
| NVIDIA NIM | build.nvidia.com 托管模型 | 加入开发者计划后可免费调用；官方未给统一 RPM，第三方汇总约 40 RPM / 无日上限，**不作承诺** | 开发者计划 + 手机验证 | [build.nvidia.com](https://build.nvidia.com/) | official_page_plus_third_party |

## 二、一次性试用额度（会到期，不能做长期方案）

| 平台 | 额度 | 有效期 / 限制 | 门槛 | 官方来源 | 核实级别 |
| --- | --- | --- | --- | --- | --- |
| 阿里云百炼 | 每模型通常 **100 万 Token**，按模型独立、不可跨模型转移 | **90 天**；仅华北2（北京）；用完自动转按量付费；控制台有「用完即停」开关，余量 20% / 耗尽时通知；Batch / 调优 / 部署不抵扣 | 实名认证 | [新人免费额度规则（2026-10-30 更新）](https://help.aliyun.com/zh/model-studio/new-free-quota) | official_docs |
| 腾讯云 TokenHub | 语言模型每模型 **50 万～100 万 Token** | **90 天**；活动截至 2026-12-31；每主账号每模型限领一次。第三方有「1 年」说法，以官方 PDF 为准 | 腾讯云账号 + 实名 | [TokenHub 产品计费 PDF](https://main.qcloudimg.com/raw/document/product/pdf/1823_130052_cn.pdf) | official_pdf |

## 三、2026 年已回收 / 本就不存在的免费层

| 平台 | 变化 | 来源级别 |
| --- | --- | --- |
| Cerebras | 2026-07-21 取消免绑卡免费层，改为绑卡后给 $5 / 30 天 | third_party_summary |
| GitHub Models | 2026-07-30 下线 | third_party_summary |
| 硅基流动 | 9B 以下小模型「永久免费」已取消，现仅 embedding / 语音 / OCR 等免费 | third_party_summary |
| DeepSeek、Together AI | 官方未提供免费层，需预付充值 | third_party_summary |

> 这一节全部是第三方汇总线索，**不是官方核实数字**，仅用于提醒「旧清单不可照抄」；动手前务必回厂商官方页确认。

## 四、跨厂商规则（写调度逻辑前先读）

- 限流常按 **RPM / TPM / RPD 三维度同时生效**，任一超限即 429，且**按项目或组织统计，不按 API Key**（Gemini、Groq、百炼官方均明确）；多开几个 Key 不会增加额度。
- 百炼按响应体区分 `Requests rate limit exceeded`（撞 RPM，退避）、`Allocated quota exceeded`（撞 TPM，省 token 或换模型）、`Request rate increased too quickly`（突发保护）。**只记状态码不记响应体，就无法判断该退避还是该换模型**。
- 免费分三种性质：长期免费层、一次性赠金、数据换额度。Gemini 免费层输入输出可能用于改进 Google 产品；处理敏感代码 / 客户数据要核对数据条款。
- 「永久免费」正在被回收，且常附实名、地域、到期日与数据授权门槛。选型应以「限流数字 + 数据条款 + 有效期」为准，而不是「送多少 token」。

## 五、如何更新这份清单

1. 逐条打开 `source_url` 复核，把变化写进 `server/data/free_tier_catalog.json`。
2. 同步 `catalog_version` 与 `server/config.py:KNOWLEDGE_VERSION`（两者必须一致，否则启动 fail-fast）。
3. 更新本文表格与「核实日期」；在 `CHANGELOG.md` 标注**口径变更，历史准确率不可直接比较**。
4. 重跑 `pytest -q`、`python -m tests.measure_performance`、`scripts/eval_runner.py`，因为评测集 `expected_keyword` 会随政策数字变化。

数据库按 `version` 隔离：旧版本条目保留但不参与检索，便于回溯「哪天口径变了」。

## 六、从清单到真正用起来

清单回答「有哪些免费模型、额度多少」；`server/data/connect_presets.json` 回答「怎么接进来」。两者用 `doc_id` 关联。

其中 7 家提供了 OpenAI 兼容预设（base_url、默认免费模型、官方注册 / Key 页、逐条操作步骤），页面「新手接入向导」会直接使用：

| 平台 | 预设 ID | base_url | 备注 |
| --- | --- | --- | --- |
| 智谱 BigModel | `zhipu` | `https://open.bigmodel.cn/api/paas/v4` | 国内首选，Flash 免费 |
| Groq | `groq` | `https://api.groq.com/openai/v1` | 必须带 `/openai/v1` |
| OpenRouter | `openrouter` | `https://openrouter.ai/api/v1` | 模型名需自己填 `:free` ID |
| 阿里云百炼 | `bailian` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 90 天试用额度 |
| Google AI Studio | `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai` | 免费层模型会换代 |
| Mistral | `mistral` | `https://api.mistral.ai/v1` | Free mode 限额以控制台为准 |
| NVIDIA NIM | `nvidia` | `https://integrate.api.nvidia.com/v1` | 模型 ID 带命名空间 |

**这些预设只是起点，不是保证**：各厂商「OpenAI 兼容」程度不一，模型名会下架，端点也可能调整。因此页面上端点 / 模型名都可编辑，且**必须先通过「测试连接」才能保存启用**；失败会按 401 / 404 / 400 / 429 / 超时分类给出中文原因与下一步动作。

不能全自动的原因很简单：免费模型都要用使用者自己的账号申请 API Key，本工具不代注册、不生成密钥、不上传密钥。密钥只存本机 SQLite，页面与日志只显示脱敏值。
