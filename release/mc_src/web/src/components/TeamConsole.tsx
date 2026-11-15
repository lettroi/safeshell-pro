import { useCallback, useEffect, useState } from 'react'
import { api, downloadAudit, getApiToken, setApiToken } from '../api'
import type { AuditReport, AuditLog, AuditVerify, Policy, ModelCatalog } from '../api'

interface Props {
  toast: (level: string, message: string) => void
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="stat">
      <div className="v">{value}</div>
      <div className="l">{label}</div>
      {sub && <div className="s">{sub}</div>}
    </div>
  )
}

export default function TeamConsole({ toast }: Props) {
  const [report, setReport] = useState<AuditReport | null>(null)
  const [logs, setLogs] = useState<AuditLog[]>([])
  const [policy, setPolicy] = useState<Policy | null>(null)
  const [models, setModels] = useState<ModelCatalog[]>([])
  const [denyText, setDenyText] = useState('')
  const [allowedText, setAllowedText] = useState('')
  const [risk, setRisk] = useState('DENY')
  const [maxTokens, setMaxTokens] = useState(512)
  const [enforce, setEnforce] = useState(true)
  const [busy, setBusy] = useState(false)
  const [toggling, setToggling] = useState<string | null>(null)
  const [verify, setVerify] = useState<AuditVerify | null>(null)
  const [verifying, setVerifying] = useState(false)
  const [tokenText, setTokenText] = useState(getApiToken())

  const load = useCallback(() => {
    api.auditReport().then(setReport).catch((e: Error) => toast('ERROR', `审计报告加载失败：${e.message}`))
    api.auditLogs(50).then(setLogs).catch((e: Error) => toast('ERROR', `审计明细加载失败：${e.message}`))
    api.auditVerify().then(setVerify).catch(() => setVerify(null))
    api.listModels().then(setModels).catch((e: Error) => toast('ERROR', `模型清单加载失败：${e.message}`))
    api.getPolicy().then((p) => {
      setPolicy(p)
      setDenyText(p.deny_patterns.join('\n'))
      setAllowedText(p.allowed_models.join('\n'))
      setRisk(p.risk_threshold)
      setMaxTokens(p.max_tokens)
      setEnforce(p.enforce_local_gate)
    }).catch((e: Error) => toast('ERROR', `策略加载失败：${e.message}`))
  }, [toast])

  useEffect(() => { load() }, [load])

  const toggleModel = async (m: ModelCatalog) => {
    if (toggling) return
    const next = !m.enabled
    let reason = ''
    if (!next) {
      const input = window.prompt(
        `停用 ${m.model} 的原因（留痕审计，例如「厂商已取消免费」）：`, '厂商已取消免费')
      if (input === null) return  // 用户取消
      reason = input
    }
    setToggling(m.model)
    try {
      await api.setModelStatus(m.model, next, reason)
      setModels((prev) => prev.map((x) => x.model === m.model ? { ...x, enabled: next } : x))
      toast('INFO', next ? `已启用 ${m.model}` : `已停用 ${m.model}：网关将拒绝该模型，客户端自动跳过`)
    } catch (e) { toast('ERROR', `操作失败：${(e as Error).message}`) }
    finally { setToggling(null) }
  }

  const checkIntegrity = async () => {
    setVerifying(true)
    try {
      const v = await api.auditVerify()
      setVerify(v)
      toast(v.ok ? 'INFO' : 'ERROR', v.message)
    } catch (e) { toast('ERROR', `校验失败：${(e as Error).message}`) }
    finally { setVerifying(false) }
  }

  const exportAudit = async (format: 'csv' | 'jsonl') => {
    try { await downloadAudit(format); toast('INFO', `审计已导出为 ${format.toUpperCase()}`) }
    catch (e) { toast('ERROR', `导出失败：${(e as Error).message}`) }
  }

  const saveToken = () => {
    setApiToken(tokenText.trim())
    toast('INFO', tokenText.trim() ? '访问令牌已保存，正在重新加载' : '已清除访问令牌')
    load()
  }

  const savePolicy = async () => {
    if (busy) return
    setBusy(true)
    const body = {
      deny_patterns: denyText.split('\n').map((s) => s.trim()).filter(Boolean),
      allowed_models: allowedText.split('\n').map((s) => s.trim()).filter(Boolean),
      risk_threshold: risk, max_tokens: maxTokens, enforce_local_gate: enforce,
    }
    try {
      const p = await api.putPolicy(body)
      setPolicy(p)
      toast('INFO', '团队策略已更新，客户端下次拉取后生效')
    } catch (e) { toast('ERROR', `保存失败：${(e as Error).message}`) }
    finally { setBusy(false) }
  }

  return (
    <div className="view">
      <div className="title-row">
        <div>
          <h1>团队控制台</h1>
          <div className="sub">用量 / 审计追溯 / 安全策略 / 模型启停 —— 全部功能免费开放</div>
        </div>
        <div className="actions">
          <span className="tag">SafeShell Pro</span>
          <button className="btn primary" onClick={load}>刷新</button>
        </div>
      </div>

      <div className="stat-grid">
        <Stat label="总调用次数" value={report ? String(report.total_calls) : '—'}
          sub={report ? `成功 ${report.ok_calls} · 失败 ${report.error_calls}` : undefined} />
        <Stat label="累计 Token" value={report ? report.total_tokens.toLocaleString() : '—'} sub="全部走免费模型" />
        <Stat label="平均延迟" value={report ? `${report.avg_latency_ms} ms` : '—'} />
        <Stat label="实付成本" value="¥ 0.00" sub="免费模型恒为 0" />
        <Stat label="预计节省" value={report ? `¥ ${report.estimated_saved_cny.toFixed(3)}` : '—'}
          sub="对照价 · 非账单（若改走付费模型大约要花多少）" />
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="head">审计完整性 / 导出 / 访问令牌 <small>哈希链防篡改 · 可选鉴权</small></div>
        <div className="body">
          <div className="inline">
            <button className="btn primary" disabled={verifying} onClick={checkIntegrity}>
              {verifying ? '校验中…' : '校验哈希链'}</button>
            <button className="btn" onClick={() => exportAudit('csv')}>导出 CSV</button>
            <button className="btn" onClick={() => exportAudit('jsonl')}>导出 JSONL</button>
            <span className={`pill ${verify?.ok ? 'p-ok' : 'p-bad'}`} style={{ marginLeft: 8 }}>
              {verify ? (verify.ok ? `链完整 · ${verify.checked} 行` : `异常 · 第 ${verify.first_bad_id} 行`) : '尚未校验'}
            </span>
          </div>
          {verify && <div className="small" style={{ marginTop: 8 }}>{verify.message}</div>}
          <div className="hr" />
          <div className="inline">
            <div className="field grow"><label>访问令牌（仅当服务端设置了 SAFESHELL_API_TOKEN 才需要）</label>
              <input type="password" value={tokenText} onChange={(e) => setTokenText(e.target.value)}
                placeholder="留空 = 本机 / 内网零配置" /></div>
            <button className="btn" onClick={saveToken}>保存令牌</button>
          </div>
        </div>
      </div>

      <div className="console-grid">
        <div className="card">
          <div className="head">成本与用量 <small>按模型拆解 · 参考价估算节省</small></div>
          <div className="body">
            <table className="tbl">
              <thead><tr><th>模型</th><th>调用</th><th>Token</th><th>节省(¥)</th></tr></thead>
              <tbody>
                {report?.by_model.map((m) => (
                  <tr key={m.model}><td className="mono">{m.model}</td><td>{m.calls}</td>
                    <td>{m.tokens.toLocaleString()}</td><td>{m.saved.toFixed(3)}</td></tr>
                ))}
                {report && report.by_model.length === 0 && (
                  <tr><td colSpan={4} className="dim">暂无数据，先用客户端发几条命令</td></tr>)}
              </tbody>
            </table>
            <div className="hr" />
            <div className="small">按模式拆解（nl2cmd=生成命令 · cmd2nl=解释命令）</div>
            <div className="chain" style={{ marginTop: 8 }}>
              {report?.by_mode.map((m) => <span key={m.mode}>{m.mode}: {m.ok}/{m.calls} 成功</span>)}
            </div>
          </div>
        </div>

        <div className="card">
          <div className="head">团队安全门控策略 <small>客户端本地强制执行</small></div>
          <div className="body">
            <div className="field"><label>禁止执行的命令/参数（每行一条正则）</label>
              <textarea rows={4} value={denyText}
                onChange={(e) => setDenyText(e.target.value)}
                placeholder={'rm -rf\\n:(){:|:&}:\\nsudo\\ndd if='} /></div>
            <div className="field"><label>允许使用的云端模型白名单（每行一个；空=不限制）</label>
              <textarea rows={3} value={allowedText}
                onChange={(e) => setAllowedText(e.target.value)}
                placeholder={'glm-4-flash\nQwen/Qwen3-8B'} /></div>
            <div className="inline">
              <div className="field grow"><label>最高放行风险等级</label>
                <select value={risk} onChange={(e) => setRisk(e.target.value)}>
                  <option value="SAFE">SAFE（仅放行安全命令）</option>
                  <option value="CAUTION">CAUTION（放行安全+需谨慎）</option>
                  <option value="DENY">DENY（拦截一切高危）</option>
                </select></div>
              <div className="field grow"><label>单次最大 token</label>
                <input type="number" min={1} max={8192} value={maxTokens}
                  onChange={(e) => setMaxTokens(Number(e.target.value))} /></div>
            </div>
            <div className="field"><label className="chk">
              <input type="checkbox" checked={enforce} onChange={(e) => setEnforce(e.target.checked)} />
              强制本地门控（客户端必须执行服务端策略）</label></div>
            <button className="btn primary" disabled={busy} onClick={savePolicy}>
              {busy ? '保存中…' : '保存策略'}</button>
            {policy && <div className="foot-note">上次更新：{policy.updated_at} · by {policy.updated_by}</div>}
          </div>
        </div>

        <div className="card" style={{ gridColumn: '1 / -1' }}>
          <div className="head">免费模型清单与启停
            <small>政策变化不再免费时，停用即可：网关硬拒绝 + 客户端自动跳过</small></div>
          <div className="body" style={{ padding: 0 }}>
            <table className="tbl wide">
              <thead><tr><th>模型</th><th>平台</th><th>接入状态</th><th>启用状态</th><th>健康度</th><th>操作</th></tr></thead>
              <tbody>
                {models.map((m) => (
                  <tr key={m.model}>
                    <td className="mono">{m.model}</td>
                    <td className="dim">{m.name}<div className="s">{m.region}</div></td>
                    <td><span className={`pill ${m.configured ? 'p-ok' : 'p-bad'}`}>
                      {m.configured ? '已接入' : '未接入'}</span></td>
                    <td><span className={`pill ${m.enabled ? 'p-ok' : 'p-bad'}`}>
                      {m.enabled ? '启用' : '已停用'}</span>
                      {m.reason && <div className="s" style={{ maxWidth: 220 }}>{m.reason}</div>}</td>
                    <td>{m.cooldown_remaining > 0
                      ? <span className="pill p-bad">熔断中 {Math.ceil(m.cooldown_remaining / 60)} 分钟</span>
                      : <span className="pill p-ok">健康</span>}
                      {m.avg_latency_ms > 0 && <div className="s">
                        {m.avg_latency_ms}ms · {(m.success_rate * 100).toFixed(0)}% 成功</div>}</td>
                    <td><button className="btn" disabled={toggling === m.model}
                      onClick={() => toggleModel(m)}>
                      {toggling === m.model ? '处理中…' : (m.enabled ? '停用' : '启用')}</button></td>
                  </tr>
                ))}
                {models.length === 0 && <tr><td colSpan={6} className="dim">暂无模型清单</td></tr>}
              </tbody>
            </table>
            <div className="foot-note" style={{ padding: '8px 14px' }}>
              停用一个模型后：网关对它的请求直接返回 403，客户端（ShellMate）拉取清单后自动从候选链移除，
              无缝切到其他仍免费的模型。重新免费后点「启用」即可恢复，无需改代码或重启。
              「熔断中」是上游超时/过载触发的临时自动冷却（5 分钟，到期自动恢复），不占用人工停用开关。
            </div>
          </div>
        </div>

        <div className="card" style={{ gridColumn: '1 / -1' }}>
          <div className="head">审计明细 <small>可追溯每次命令的生成来源、模型、延迟与成本</small></div>
          <div className="body" style={{ padding: 0 }}>
            <table className="tbl wide">
              <thead><tr><th>时间</th><th>客户端</th><th>模型</th><th>模式</th>
                <th>Token</th><th>延迟</th><th>状态</th><th>节省(¥)</th></tr></thead>
              <tbody>
                {logs.map((l) => (
                  <tr key={l.id}>
                    <td className="mono dim">{new Date(l.ts).toLocaleTimeString('zh-CN', { hour12: false })}</td>
                    <td className="mono">{l.client_id}</td>
                    <td className="mono">{l.model}</td>
                    <td>{l.mode}</td>
                    <td>{l.total_tokens}</td>
                    <td>{l.latency_ms}ms</td>
                    <td><span className={`pill ${l.ok ? 'p-ok' : 'p-bad'}`}>{l.ok ? 'OK' : (l.error_code || 'ERR')}</span></td>
                    <td>{l.saved.toFixed(3)}</td>
                  </tr>
                ))}
                {logs.length === 0 && <tr><td colSpan={8} className="dim">暂无审计记录</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}
