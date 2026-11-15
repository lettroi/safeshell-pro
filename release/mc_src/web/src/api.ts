export type Status = 'active' | 'exhausted' | 'warning' | 'available' | 'unlimited' | 'limited'

export interface Provider {
  id: string; name: string; vendor: string; unit: string
  used: number; limit: number; remaining: number; remaining_pct: number
  usage_label: string; reset_at: string; endpoint: string; tier: string
  free: string; rpm: number; status: Status
  unlimited: boolean; limited: boolean; enabled: boolean; key_hint: string
}

export interface Strategy {
  current_id: string; previous_id: string | null
  auto_switch: boolean; threshold: number; chain: string[]
  notify_in_app: boolean; notify_email: boolean; notify_webhook: boolean
  switch_count: number; avoided: number
}

export interface EventItem {
  id: number; ts: string; level: string; kind: string; message: string
  provider_id: string | null; request_id: string | null
}

export interface LogItem {
  request_id: string; ts: string; intent: string; model: string
  prompt_version: string; latency_ms: number; ttft_ms: number
  token_usage: number; error_code: string | null
  sources: string[]; routed_by: string | null
}

export interface KPI {
  current_model: string; remaining_label: string; available_sources: number
  today_switches: number; avoided_requests: number; today_tokens: number
}

export interface TrendPoint { hour: string; used: number }

export interface Overview {
  providers: Provider[]; strategy: Strategy; events: EventItem[]
  logs: LogItem[]; kpi: KPI; trend: TrendPoint[]; demo_mode: boolean
}

export interface Source { doc_id: string; title: string; url: string; score: number }
export interface CatalogMeta {
  catalog_version: string; last_verified: string; entry_count: number
  official_count: number; scope_note: string
}
export interface DiscoverResult {
  hits: Source[]; answer: string; error_code: string | null; catalog?: CatalogMeta | null
}
export interface ConnectPreset {
  id: string; name: string; doc_id: string; recommended: boolean; region: string
  api_base: string; model: string; model_help?: string | null; free_policy: string
  signup_url: string; key_url: string; docs_url: string; steps: string[]; notes: string
  configured: boolean; key_masked?: string | null; updated_at?: string | null
}
export interface ConnectStatus {
  id: string; name: string; configured: boolean; api_base: string; model: string
  key_masked?: string | null; updated_at?: string | null
}
export interface ConnectTestResult {
  ok: boolean; latency_ms: number; model: string; error_code: string | null
  message: string; hint: string; preview: string
}
export interface Stage { stage: string; elapsed_ms: number; detail: string }

export interface ChatResponse {
  answer: string; sources: Source[]; latency_ms: number; ttft_ms: number
  request_id: string; prompt_version: string; token_usage: number
  model: string; intent: string; error_code: string | null
  fallback_from: string | null; routed_by: string | null
  stages: Stage[]; billed_amount: number; current_provider: string
}

export interface ChatPayload {
  user_id: string; message: string; conversation_id?: string
  request_id?: string; inject?: 'timeout' | 'rate_limit' | 'parse_error' | 'no_context' | null
  provider_id?: string | null
  history?: { role: 'user' | 'assistant'; content: string }[]
}

export interface Message {
  id: number; role: string; content: string; model: string | null
  request_id: string | null; sources: string[]; created_at: string
}

export interface Conversation {
  id: string; title: string; total_tokens: number
  request_count: number; switch_count: number; updated_at: string
}

export interface ConversationDetail { conversation: Conversation; messages: Message[] }

export interface SimulateResult {
  request_id: string; from_provider: string; to_provider: string
  auto_switch: boolean; replay_status: number; message: string; events: EventItem[]
}

// ---- SafeShell Pro：审计 / 成本 / 团队策略 ----
export interface AuditModelBreakdown { model: string; calls: number; tokens: number; saved: number }
export interface AuditModeBreakdown { mode: string; calls: number; ok: number; err: number }
export interface AuditReport {
  total_calls: number; ok_calls: number; error_calls: number
  total_tokens: number; total_prompt_tokens: number; total_completion_tokens: number
  avg_latency_ms: number; estimated_saved_cny: number; billed_cny: number
  by_model: AuditModelBreakdown[]; by_mode: AuditModeBreakdown[]; client_id: string | null
}
export interface AuditLog {
  id: number; ts: string; client_id: string; model: string; mode: string
  prompt_tokens: number; completion_tokens: number; total_tokens: number
  latency_ms: number; ok: boolean; error_code: string | null
  risk: string | null; command_hash: string | null; billed: number; saved: number
  request_id: string | null; prev_hash?: string | null; row_hash?: string | null
}
export interface AuditVerify {
  ok: boolean; checked: number; total: number; legacy_rows: number
  first_bad_id: number | null; message: string; sampled: boolean
}
export interface Policy {
  deny_patterns: string[]; risk_threshold: string; allowed_models: string[]
  max_tokens: number; enforce_local_gate: boolean; updated_at: string; updated_by: string
}
export interface ModelCatalog {
  model: string; provider_id: string; name: string; region: string
  configured: boolean; enabled: boolean; cooldown_remaining: number
  reason: string; avg_latency_ms: number; success_rate: number
}

export interface SSEStage {
  event: 'stage'; stage: string; request_id: string; elapsed_ms: number; detail: string
}
export interface SSENotice {
  event: string; level?: string; kind?: string; message: string
  request_id?: string; from_provider?: string; to_provider?: string
}
export type SSEEvent = SSEStage | SSENotice | { event: 'connected'; status: string }

const BASE = '/api/v1'
const TOKEN_KEY = 'safeshell_api_token'

// 可选共享令牌：本机/内网零配置时为空，不影响任何请求；
// 只有管理员在服务端设置了 SAFESHELL_API_TOKEN 才需要在控制台填一次。
export const getApiToken = () => localStorage.getItem(TOKEN_KEY) || ''
export const setApiToken = (t: string) => {
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getApiToken()
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'X-API-Token': token } : {}),
      ...(init?.headers || {}),
    },
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try { detail = (await res.json()).detail || detail } catch { /* ignore */ }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  overview: () => request<Overview>('/overview'),
  providers: () => request<Provider[]>('/providers'),
  strategy: () => request<Strategy>('/strategy'),
  patchStrategy: (body: Partial<Strategy>) =>
    request<Strategy>('/strategy', { method: 'PATCH', body: JSON.stringify(body) }),
  chat: (body: ChatPayload) =>
    request<ChatResponse>('/chat', { method: 'POST', body: JSON.stringify(body) }),
  simulate429: () => request<SimulateResult>('/simulate/429', { method: 'POST' }),
  switch: (provider_id: string, reason: 'manual' | 'undo' = 'manual') =>
    request<{ ok: boolean; message: string }>('/switch', {
      method: 'POST', body: JSON.stringify({ provider_id, reason }),
    }),
  refresh: () => request<{ ok: boolean }>('/refresh', { method: 'POST' }),
  conversations: () => request<Conversation[]>('/conversations'),
  conversation: (id: string) => request<ConversationDetail>(`/conversations/${id}`),
  logs: (limit = 40) => request<LogItem[]>(`/logs?limit=${limit}`),
  connectPresets: () => request<ConnectPreset[]>('/connect/presets'),
  connectStatus: () => request<ConnectStatus[]>('/connect/status'),
  connectTest: (body: Record<string, unknown>) =>
    request<ConnectTestResult>('/connect/test', { method: 'POST', body: JSON.stringify(body) }),
  connectConfigure: (body: Record<string, unknown>) =>
    request<{ ok: boolean; message: string; data: Record<string, unknown> }>('/connect/configure', {
      method: 'POST', body: JSON.stringify(body),
    }),
  connectRemove: (id: string) =>
    request<{ ok: boolean }>(`/connect/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  discover: (q: string) => request<DiscoverResult>(
    `/discover?q=${encodeURIComponent(q)}`),
  addProvider: (body: Record<string, unknown>) =>
    request<Provider>('/providers', { method: 'POST', body: JSON.stringify(body) }),
  feedback: (request_id: string, vote: 'up' | 'down', reason: string) =>
    request<{ ok: boolean }>('/feedback', {
      method: 'POST', body: JSON.stringify({ request_id, vote, reason }),
    }),
  auditReport: (clientId?: string) =>
    request<AuditReport>(`/audit/report${clientId ? `?client_id=${encodeURIComponent(clientId)}` : ''}`),
  auditLogs: (limit = 50, clientId?: string) =>
    request<AuditLog[]>(`/audit/logs?limit=${limit}${clientId ? `&client_id=${encodeURIComponent(clientId)}` : ''}`),
  auditVerify: (limit?: number) =>
    request<AuditVerify>(`/audit/verify${limit ? `?limit=${limit}` : ''}`),
  getPolicy: () => request<Policy>('/policy'),
  putPolicy: (body: Partial<Policy>) =>
    request<Policy>('/policy', { method: 'PUT', body: JSON.stringify(body) }),
  listModels: () => request<ModelCatalog[]>('/models'),
  setModelStatus: (model: string, enabled: boolean, reason: string) =>
    request<ModelCatalog>('/models/status', {
      method: 'PUT', body: JSON.stringify({ model, enabled, reason }),
    }),
}

// 导出走 blob 下载（需要带令牌头，不能用普通 <a href>）
export async function downloadAudit(format: 'csv' | 'jsonl'): Promise<void> {
  const token = getApiToken()
  const res = await fetch(`${BASE}/audit/export?format=${format}`, {
    headers: token ? { 'X-API-Token': token } : {},
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `safeshell-audit.${format}`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export function fmtTime(ts: string): string {
  if (!ts) return '--:--:--'
  const d = new Date(ts)
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleTimeString('zh-CN', { hour12: false })
}
