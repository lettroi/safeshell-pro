import { useState } from 'react'
import type { CatalogMeta, Source } from '../api'
import { api } from '../api'

interface Props {
  onClose: () => void
  reload: () => void
  toast: (level: string, message: string) => void
}

const isExternal = (url: string) => /^https?:\/\//.test(url)

export default function DiscoverDrawer({ onClose, reload, toast }: Props) {
  const [q, setQ] = useState('Gemini 免费层')
  const [hits, setHits] = useState<Source[]>([])
  const [answer, setAnswer] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [catalog, setCatalog] = useState<CatalogMeta | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ id: '', name: '', vendor: 'Demo Vendor', limit: '1000000' })

  const search = async () => {
    try {
      const r = await api.discover(q)
      setHits(r.hits); setAnswer(r.answer); setError(r.error_code); setCatalog(r.catalog ?? null)
      if (r.error_code) toast('INFO', '检索为空，已短路返回 NO_CONTEXT（不编造政策）')
    } catch (e) { toast('ERROR', `检索失败：${(e as Error).message}`) }
  }

  const add = async () => {
    if (!form.id || !form.name) { toast('ERROR', '请填写 ID 与名称'); return }
    try {
      await api.addProvider({
        id: form.id, name: form.name, vendor: form.vendor, unit: 'tokens',
        limit: Number(form.limit), reset_at: '每月 1 日',
        endpoint: `https://example.local/v1/chat/completions`,
        tier: '按月', free: '演示额度', rpm: 30, unlimited: false,
      })
      toast('ROUTE', `已加入本地额度表：${form.name}（演示记录，未发起真实调用）`)
      setShowForm(false); reload()
    } catch (e) { toast('ERROR', `接入失败：${(e as Error).message}`) }
  }

  return (
    <>
      <div className="mask" onClick={onClose} />
      <div className="drawer">
        <div className="head"><b>发现免费 API</b><button className="btn sm" onClick={onClose}>关闭</button></div>
        <div className="content">
          <div className="sub" style={{ marginBottom: 12 }}>
            {catalog
              ? `政策清单 ${catalog.catalog_version} · 核实 ${catalog.last_verified} · ${catalog.entry_count} 条（${catalog.official_count} 条来自官方文档 / PDF）`
              : '政策清单元信息加载中…'}
            <br />
            静态快照，运行时不联网；官方未公布的数字均标注「以控制台为准」，请点来源复核。命中为空时短路返回，不编造政策。
          </div>
          <div className="inline" style={{ marginBottom: 12 }}>
            <input className="search" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && search()} />
            <button className="btn primary" onClick={search}>搜索</button>
          </div>
          {error && <div className="alert amber"><div className="txt">{answer}<div className="small">error_code: {error} · 可修改关键词重试</div></div></div>}
          {!error && answer && <div className="small" style={{ marginBottom: 10 }}>{answer}</div>}
          {hits.map((h) => (
            <div className="discover-item" key={h.doc_id}>
              <b>{h.title}</b>
              <p>
                source: {h.doc_id} · score {h.score} ·{' '}
                {isExternal(h.url)
                  ? <a href={h.url} target="_blank" rel="noreferrer">查看官方来源</a>
                  : <span>仓库内文档：{h.url}</span>}
              </p>
              <button className="btn sm" onClick={() => { setShowForm(true); setForm((f) => ({ ...f, name: h.title })) }}>加入本地额度表</button>
            </div>
          ))}
          {!hits.length && !error && <div className="empty">输入关键词开始检索，例如 Gemini / Groq / 百炼 / 已取消</div>}

          <div className="hr" />
          <button className="btn ghost" onClick={() => setShowForm((v) => !v)}>手动新增数据源</button>
          {showForm && (
            <div style={{ marginTop: 12 }}>
              {(['id', 'name', 'vendor', 'limit'] as const).map((k) => (
                <div className="field" key={k}><label>{k}</label>
                  <input value={form[k]} onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))} />
                </div>
              ))}
              <button className="btn primary" onClick={add}>确认新增</button>
              <div className="foot-note">此操作只在本地 SQLite 新增一条演示记录：endpoint 为占位、不配置密钥、不会向厂商发起调用。真实接入需另配服务端密钥与上游适配。</div>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
