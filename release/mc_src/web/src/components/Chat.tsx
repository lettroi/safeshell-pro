import { useCallback, useEffect, useRef, useState } from 'react'
import type { ViewProps } from '../App'
import type { ChatResponse, Conversation, ConversationDetail } from '../api'
import { api } from '../api'
import StagesPanel from './StagesPanel'
import ErrorPanel from './ErrorPanel'

export default function ChatView({ overview, reload, toast, stages, activeIdRef, resetStages }: ViewProps) {
  const [convs, setConvs] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState('c1')
  const [detail, setDetail] = useState<ConversationDetail | null>(null)
  const [text, setText] = useState('')
  const [pending, setPending] = useState(false)
  const [result, setResult] = useState<ChatResponse | null>(null)
  const [search, setSearch] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const lastRid = useRef<string | null>(null)
  const lastMessage = useRef('')

  const loadConvs = useCallback(() => {
    api.conversations().then(setConvs).catch(() => { /* 保持旧列表 */ })
  }, [])
  const loadDetail = useCallback((id: string) => {
    api.conversation(id).then(setDetail).catch(() => setDetail(null))
  }, [])

  useEffect(() => { loadConvs(); loadDetail(activeId) }, [loadConvs, loadDetail, activeId])

  const newConversation = () => {
    const id = `c-${Date.now().toString(36)}`
    setActiveId(id); setDetail(null); setResult(null)
    toast('INFO', '已新建会话（首次发消息时落库）')
  }

  const send = async (ridOverride?: string, textOverride?: string) => {
    const content = (textOverride ?? text).trim()
    if (!content || pending) return
    const rid = ridOverride || `req-chat-${Date.now().toString(36)}`
    lastRid.current = rid
    if (textOverride === undefined) lastMessage.current = content
    activeIdRef.current = rid
    resetStages()
    setPending(true)
    setResult(null)
    setText('')
    try {
      const r = await api.chat({ user_id: 'dev_chat', message: content, conversation_id: activeId, request_id: rid })
      setResult(r)
      loadDetail(activeId); loadConvs(); reload()
      if (!r.error_code) toast('INFO', `回答完成 · ${r.latency_ms}ms · ${r.token_usage} tokens`)
    } catch (e) {
      toast('ERROR', `请求失败：${(e as Error).message}`)
    } finally {
      setPending(false)
      activeIdRef.current = null
    }
  }

  const undo = async () => {
    try { await api.switch('', 'undo'); toast('ROUTE', '已撤销上一次切换'); loadDetail(activeId); reload() }
    catch (e) { toast('ERROR', `撤销失败：${(e as Error).message}`) }
  }
  const switchTo = async (id: string) => {
    try { await api.switch(id); toast('ROUTE', `已切换到 ${id}`); loadDetail(activeId); reload() }
    catch (e) { toast('ERROR', `切换失败：${(e as Error).message}`) }
  }

  const current = overview.providers.find((p) => p.id === overview.strategy.current_id)
  const switchable = [...overview.providers]
    .filter((p) => p.id !== overview.strategy.current_id && p.status !== 'exhausted' && p.status !== 'limited')
    .sort((a, b) => b.remaining_pct - a.remaining_pct)

  return (
    <div className="view">
      <div className="title-row">
        <div><h1>对话测试台</h1><div className="sub">自动切换时上下文与历史消息完整保留，对话流内插入可撤销的系统提示卡</div></div>
        <div className="actions"><button className="btn" onClick={newConversation}>新建会话</button></div>
      </div>

      <div className="chat-grid">
        <div className="card conv-col">
          <div className="head">会话列表 <small>{convs.length}</small></div>
          <div style={{ padding: 10, borderBottom: '1px solid var(--line)' }}>
            <input className="search" placeholder="搜索会话…" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <div className="conv-list">
            {convs.filter((c) => c.title.includes(search)).map((c) => (
              <div key={c.id} className={`conv ${c.id === activeId ? 'on' : ''}`} onClick={() => { setActiveId(c.id); setResult(null) }}>
                {c.title}<small>{c.total_tokens} tokens · {c.request_count} 次请求</small>
              </div>
            ))}
          </div>
        </div>

        <div className="card chat-col">
          <div className="head">当前模型状态 <span className="pill p-active" style={{ marginLeft: 8 }}>在线</span>
            <small>{current?.name} · 剩余 {current?.usage_label}</small></div>
          <div className="chat-scroll">
            {(detail?.messages || []).map((m) => m.role === 'system' ? (
              <div className="sys" key={m.id}><b>已自动切换模型</b> · 上下文完整保留<br />{m.content} <button onClick={undo}>撤销切换</button></div>
            ) : (
              <div className={`msg ${m.role}`} key={m.id}>
                <div className="who"><span>{m.role === 'user' ? 'YOU' : 'ASSISTANT'}</span><span>{m.model || ''}</span></div>
                {m.content}
                {m.sources.length > 0 && <div className="src">sources: [{m.sources.join(', ')}]</div>}
              </div>
            ))}
            {pending && <StagesPanel stages={stages} />}
            {result?.error_code && !pending && (
              <ErrorPanel result={result} toast={toast}
                onRetry={() => send(lastRid.current || undefined, lastMessage.current)}
                onEdit={() => inputRef.current?.focus()} />
            )}
            {!pending && result && !result.error_code && (
              <div className="msg assistant">
                <div className="who"><span>ASSISTANT · {result.model}</span><span>{result.latency_ms}ms · {result.token_usage} tok</span></div>
                {result.answer}
                {result.sources.length > 0 && <div className="src">sources: [{result.sources.map((s) => s.doc_id).join(', ')}] · 已校验来自本次检索</div>}
                <div style={{ marginTop: 8, display: 'flex', gap: 6 }}>
                  <button className="btn sm" onClick={async () => { await navigator.clipboard.writeText(result.answer); toast('INFO', '回答已复制') }}>复制回答</button>
                  <button className="btn sm" onClick={() => send()}>重新生成</button>
                  <button className="btn sm" onClick={async () => { await api.feedback(result.request_id, 'up', ''); toast('INFO', '已记录赞') }}>赞</button>
                  <button className="btn sm" onClick={async () => { await api.feedback(result.request_id, 'down', '用户点踩'); toast('INFO', '已记录踩') }}>踩</button>
                </div>
              </div>
            )}
          </div>
          <div className="inputbar">
            <textarea ref={inputRef} placeholder="输入消息，Enter 发送 / Shift+Enter 换行…" value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} />
            <button className="btn primary" disabled={pending || !text.trim()} onClick={() => send()}>{pending ? '生成中…' : '发送'}</button>
          </div>
        </div>

        <div className="card right">
          <div className="head">本会话用量 <small>{detail?.conversation.id || activeId}</small></div>
          <div className="body">
            <div className="kpi" style={{ marginBottom: 10 }}><label>TOKENS 合计</label><b>{detail?.conversation.total_tokens || 0}</b></div>
            <div className="kpi" style={{ marginBottom: 10 }}><label>请求数 / 自动切换</label><b style={{ fontSize: 13 }}>{detail?.conversation.request_count || 0} / {detail?.conversation.switch_count || 0}</b></div>
            <div className="kpi"><label>本次延迟 / 计费</label><b style={{ fontSize: 13 }}>{result ? `${result.latency_ms} ms` : '—'} · ¥0.00</b></div>
            <div className="hr" />
            <div className="small" style={{ marginBottom: 8 }}>可切换模型（按剩余额度）</div>
            {switchable.map((p) => (
              <div key={p.id} className="row-flex" style={{ padding: '6px 0', borderBottom: '1px solid var(--line)' }}>
                <span className={`dot ${p.remaining_pct <= 10 ? 'red' : 'green'}`} />
                <div className="grow"><div>{p.name}</div><div className="small">{p.remaining_pct}% · {p.free}</div></div>
                <button className="btn sm" onClick={() => switchTo(p.id)}>切换</button>
              </div>
            ))}
            <div className="foot-note">额度表与用量趋势为演示数据；接入真实模型后，本页回答来自你选的厂商，计费与限额以厂商控制台为准。</div>
          </div>
        </div>
      </div>
    </div>
  )
}
