import { useMemo, useState } from 'react'
import type { ViewProps } from '../App'
import type { Provider } from '../api'
import { api, fmtTime } from '../api'
import { STATUS_TEXT } from '../stageText'

const FILTERS = [
  { id: 'all', label: '全部' }, { id: 'available', label: '可用' },
  { id: 'warning', label: '即将耗尽' }, { id: 'exhausted', label: '已耗尽' },
]

function TrendChart({ points }: { points: { hour: string; used: number }[] }) {
  const max = Math.max(1, ...points.map((p) => p.used))
  const w = 300, h = 96
  const step = w / Math.max(1, points.length - 1)
  const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${i * step},${h - (p.used / max) * (h - 8)}`).join(' ')
  return (
    <svg className="trend" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-label="24h 消耗趋势">
      <line x1="0" y1={h - 1} x2={w} y2={h - 1} stroke="#27272A" />
      <path d={`${d} L${w},${h} L0,${h} Z`} fill="rgba(242,201,76,.10)" />
      <path d={d} fill="none" stroke="#F2C94C" strokeWidth="1.6" />
    </svg>
  )
}

function Bar({ pct, status }: { pct: number; status: string }) {
  const cls = status === 'exhausted' || pct <= 0 ? 'red' : pct <= 10 ? 'amber' : 'green'
  return <span className="bar"><i className={cls} style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} /></span>
}

export default function OverviewView({ overview, reload, toast, openDrawer }: ViewProps) {
  const [filter, setFilter] = useState('all')
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const { providers, strategy, events, kpi, trend } = overview

  const rows = useMemo(() => providers.filter((p) => {
    const hit = `${p.name} ${p.vendor} ${p.id}`.toLowerCase().includes(q.toLowerCase())
    if (!hit) return false
    if (filter === 'available') return ['available', 'active', 'unlimited'].includes(p.status)
    if (filter === 'warning') return p.status === 'warning' || p.status === 'limited'
    if (filter === 'exhausted') return p.status === 'exhausted'
    return true
  }), [providers, filter, q])

  const exhausted = providers.find((p) => p.status === 'exhausted')
  const current = providers.find((p) => p.id === strategy.current_id)

  const doRefresh = async () => {
    setBusy(true)
    try { await api.refresh(); toast('INFO', '额度轮询完成'); reload() }
    catch (e) { toast('ERROR', `刷新失败：${(e as Error).message}`) }
    finally { setBusy(false) }
  }
  const patch = async (body: Record<string, unknown>, label: string) => {
    try { await api.patchStrategy(body); toast('ROUTE', `${label}已保存`); reload() }
    catch (e) { toast('ERROR', `保存失败：${(e as Error).message}`) }
  }
  const setDefault = async (p: Provider) => {
    try { await api.switch(p.id); toast('ROUTE', `已切换到 ${p.name}`); reload() }
    catch (e) { toast('ERROR', `切换失败：${(e as Error).message}`) }
  }
  const simulate = async () => {
    try { const r = await api.simulate429(); toast('ROUTE', r.message); reload() }
    catch (e) { toast('ERROR', `演练失败：${(e as Error).message}`) }
  }

  return (
    <div className="view">
      <div className="title-row">
        <div><h1>额度总览与模型切换</h1><div className="sub">发现免费额度 → 实时监控用量 → 耗尽前预警 → 耗尽后自动切换，且调用与对话不中断</div></div>
        <div className="actions">
          <button className="btn" onClick={doRefresh} disabled={busy}>{busy ? '刷新中…' : '刷新额度'}</button>
          <button className="btn ghost" onClick={openDrawer}>发现免费 API</button>
          <button className="btn primary" onClick={simulate}>注入一次 429</button>
        </div>
      </div>

      {exhausted && (
        <div className="alert">
          <span className="pill p-exhausted">额度耗尽</span>
          <div className="txt"><b>{exhausted.name}</b> 免费额度已用尽 · 已自动切换至 <b>{current?.name || strategy.current_id}</b>，请求未中断</div>
          <button className="btn sm" onClick={() => { location.hash = 'console'; }}>查看切换记录</button>
        </div>
      )}

      <div className="kpis">
        <div className="kpi"><label>当前生效模型</label><b>{current?.name || strategy.current_id}</b><span>上一模型 {strategy.previous_id || '—'} · 可撤销</span></div>
        <div className="kpi"><label>本月剩余免费额度</label><b className="mono">{current?.usage_label || '—'}</b><span>单位口径已归一化 · {current?.unit}</span></div>
        <div className="kpi"><label>可用 API 源</label><b>{kpi.available_sources} / {providers.length}</b><span>数据源在线 · MOCK 模式</span></div>
        <div className="kpi"><label>今日自动切换</label><b>{kpi.today_switches}</b><span>避免中断 {kpi.avoided_requests} 次请求 · {kpi.today_tokens} tokens</span></div>
      </div>

      <div className="grid-overview">
        <div className="card">
          <div className="head">实时额度监控 <small>6s 轮询 · 服务端状态机</small></div>
          <div className="toolbar">
            {FILTERS.map((f) => <button key={f.id} className={`chip ${filter === f.id ? 'on' : ''}`} onClick={() => setFilter(f.id)}>{f.label}</button>)}
            <input className="search" placeholder="搜索模型 / 供应商…" value={q} onChange={(e) => setQ(e.target.value)} />
            <button className="btn sm" onClick={doRefresh}>刷新</button>
          </div>
          <table>
            <thead><tr><th>模型 / 供应商</th><th>免费额度（已用 / 上限）</th><th>剩余额度</th><th>重置时间</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id}>
                  <td><div className="row-flex"><span className={`dot ${p.status === 'exhausted' ? 'red' : 'green'}`} /><div><div>{p.name}</div><div className="small">{p.vendor} · {p.rpm} RPM</div></div></div></td>
                  <td className="mono">{p.usage_label}</td>
                  <td><div className="row-flex"><Bar pct={p.remaining_pct} status={p.status} /><span className="mono">{p.unlimited ? '∞' : `${p.remaining_pct}%`}</span></div></td>
                  <td className="small">{p.reset_at}</td>
                  <td><span className={`pill p-${p.status}`}>{STATUS_TEXT[p.status]}</span></td>
                  <td><button className="btn sm" disabled={p.status === 'exhausted' || p.id === strategy.current_id} onClick={() => setDefault(p)}>设为默认</button></td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={6}><div className="empty">没有匹配的数据源</div></td></tr>}
            </tbody>
          </table>
          <div className="tfoot"><span>共 {rows.length} / {providers.length} 个数据源</span><span>额度为演示数据 · 政策以官方清单为准</span><span>单位口径：RPD / TPM / tokens / 不限量</span></div>
        </div>

        <div className="stack">
          <div className="card">
            <div className="head">自动切换策略 <small>threshold &amp; chain</small></div>
            <div className="body">
              <div className="switch">
                <button className={`toggle ${strategy.auto_switch ? 'on' : ''}`} onClick={() => patch({ auto_switch: !strategy.auto_switch }, '自动切换开关')} aria-label="自动切换总开关"><i /></button>
                <div><div>自动切换总开关</div><div className="small">耗尽时自动选备胎并重放请求</div></div>
              </div>
              <div className="field"><label>预警阈值（剩余比例）</label>
                <div className="inline"><input type="number" min={0.01} max={1} step={0.01} value={strategy.threshold} onChange={(e) => patch({ threshold: Number(e.target.value) }, '预警阈值')} style={{ width: 90 }} /><span className="small">当前 {Math.round(strategy.threshold * 100)}%</span></div>
              </div>
              <div className="field"><label>备胎优先级</label><div className="chain">{strategy.chain.map((c, i) => <span key={c}>{i + 1}. {c}</span>)}</div></div>
              <div className="hr" />
              <div className="small">通知渠道（本版本只记录意图，不真实投递）</div>
              {(['notify_in_app', 'notify_email', 'notify_webhook'] as const).map((key) => (
                <div className="switch" key={key} style={{ marginTop: 8, marginBottom: 0 }}>
                  <button className={`toggle ${strategy[key] ? 'on' : ''}`} onClick={() => patch({ [key]: !strategy[key] }, '通知渠道')}><i /></button>
                  <span className="small">{key === 'notify_in_app' ? '站内通知' : key === 'notify_email' ? '邮件' : 'Webhook'}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="head">24h 消耗趋势 <small>DEMO 曲线</small></div>
            <div className="body"><TrendChart points={trend} /><div className="foot-note">按小时聚合的演示数据，seed={20261112}；真实产品需按归一化单位统计。</div></div>
          </div>

          <div className="card">
            <div className="head">告警与切换记录 <small>{events.length} 条</small></div>
            <div className="body events">
              {events.map((e) => (
                <div className="event" key={e.id}>
                  <span className={`lv ${e.level}`}>{e.level}</span>
                  <span className="msg">{e.message}</span>
                  <span className="ts">{fmtTime(e.ts)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
