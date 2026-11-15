/** 后端真实阶段 ↔ 前端加载文案（唯一来源，禁止用定时器模拟假进度）。 */
export const STAGE_ORDER = ['parse', 'retrieve', 'quota_check', 'route', 'generate', 'respond'] as const

export const STAGE_TEXT: Record<string, { label: string; hint: string }> = {
  parse: { label: '正在解析请求与意图', hint: '预处理 / 输入清洗 / 意图分流' },
  retrieve: { label: '正在检索免费额度知识库', hint: 'Top-K 命中与 metadata 过滤' },
  quota_check: { label: '正在检查数据源额度与阈值', hint: '轮询快照 / 阈值预警 / 耗尽去重' },
  route: { label: '正在选择可用模型 / 触发切换', hint: '备胎链路 / 429 重放决策' },
  generate: { label: '正在生成回答', hint: 'Prompt 组装 / 上游调用' },
  respond: { label: '响应就绪', hint: '后处理 / 来源校验 / 脱敏' },
}

export interface ErrorAction { label: string; action: 'retry' | 'feedback' | 'docs' | 'edit' }

export interface ErrorUI {
  title: string; desc: string; actions: ErrorAction[]; scenario: 'timeout' | 'hallucination' | 'other'
}

/** 异常态文案集中配置，避免散落在组件里；每个异常态至少 1 个可点击动作。 */
export const ERROR_UI: Record<string, ErrorUI> = {
  LLM_TIMEOUT: {
    title: '服务繁忙，请稍后重试', scenario: 'timeout',
    desc: '上游模型在超时时间内未返回。已保留你的原始提问与上下文，可以直接重试。',
    actions: [{ label: '重试', action: 'retry' }, { label: '反馈', action: 'feedback' }],
  },
  UPSTREAM_ERROR: {
    title: '上游服务暂时不可用', scenario: 'other',
    desc: '请求已记录，可稍后重试或切换到备用数据源。',
    actions: [{ label: '重试', action: 'retry' }, { label: '反馈', action: 'feedback' }],
  },
  RATE_LIMITED: {
    title: '请求过于频繁，请稍后重试', scenario: 'other',
    desc: '已触发 429 兜底；网关会尝试切换数据源并重放请求。',
    actions: [{ label: '重试', action: 'retry' }, { label: '反馈', action: 'feedback' }],
  },
  PARSE_ERROR: {
    title: '结果格式异常，已记录，请重试', scenario: 'other',
    desc: '模型输出解析失败，已降级为兜底文案，原始片段写入结构化日志。',
    actions: [{ label: '重试', action: 'retry' }, { label: '反馈', action: 'feedback' }],
  },
  NO_CONTEXT: {
    title: '知识库中未找到相关信息', scenario: 'hallucination',
    desc: '本地政策知识库没有可支撑该问题的文档，已短路返回，不会编造免费额度政策。建议补充关键词或查阅官方文档。',
    actions: [{ label: '修改输入', action: 'edit' }, { label: '反馈', action: 'feedback' },
              { label: '查阅官方文档', action: 'docs' }],
  },
  UNSUPPORTED_CLAIM: {
    title: '我无法确定该答案的准确性', scenario: 'hallucination',
    desc: '回答缺少可核验来源，已拒绝展示未证实结论，建议查阅厂商官方文档。',
    actions: [{ label: '反馈', action: 'feedback' }, { label: '查阅官方文档', action: 'docs' }],
  },
  UNSAFE_INPUT: {
    title: '该请求包含不被允许的内容，已拒绝处理', scenario: 'other',
    desc: '安全规则命中，未调用检索与模型。请修改输入后重试。',
    actions: [{ label: '修改输入', action: 'edit' }, { label: '反馈', action: 'feedback' }],
  },
  EMPTY_INPUT: {
    title: '输入为空，请重新输入', scenario: 'other',
    desc: '请求未进入流水线。', actions: [{ label: '修改输入', action: 'edit' }],
  },
}

export function errorUI(code: string | null): ErrorUI {
  if (!code) return ERROR_UI.NO_CONTEXT
  return ERROR_UI[code] || {
    title: '请求未完成', scenario: 'other', desc: `错误码：${code}，已写入结构化日志。`,
    actions: [{ label: '重试', action: 'retry' }, { label: '反馈', action: 'feedback' }],
  }
}

export const STATUS_TEXT: Record<string, string> = {
  active: '生效中', exhausted: '已耗尽', warning: '即将耗尽',
  available: '可用', unlimited: '不限量', limited: '限流中',
}
