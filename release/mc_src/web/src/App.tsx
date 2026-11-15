import { useCallback, useEffect, useRef, useState } from 'react'
import type { Overview } from './api'
import { api } from './api'
import { useEventStream } from './useEventStream'
import type { Toast } from './useEventStream'
import OverviewView from './components/Overview'
import ConsoleView from './components/Console'
import ChatView from './components/Chat'
import DiscoverDrawer from './components/DiscoverDrawer'
import Onboarding, { GuideBanner } from './components/Onboarding'
import TeamConsole from './components/TeamConsole'
import Landing from './components/Landing'

export type ViewName = 'landing' | 'guide' | 'overview' | 'console' | 'chat' | 'team'

const NAV: { group: string; items: { id: ViewName; label: string }[] }[] = [
  { group: 'PRODUCT', items: [
    { id: 'landing', label: '产品与定价' },
  ] },
  { group: 'START HERE', items: [
    { id: 'guide', label: '新手接入向导' },
  ] },
  { group: 'WORKSPACE', items: [
    { id: 'overview', label: '额度总览' }, { id: 'console', label: '调用控制台' },
    { id: 'chat', label: '对话测试台' }, { id: 'team', label: '团队控制台' },
  ] },
]

export interface ViewProps {
  overview: Overview
  reload: () => void
  toast: (level: string, message: string) => void
  stages: { stage: string; elapsed_ms: number; detail: string }[]
  activeIdRef: React.MutableRefObject<string | null>
  resetStages: () => void
  openDrawer: () => void
}

export default function App() {
  const [view, setView] = useState<ViewName>(() => {
    const h = location.hash.replace('#', '')
    const valid: ViewName[] = ['landing', 'guide', 'overview', 'console', 'chat', 'team']
    return valid.includes(h as ViewName) ? (h as ViewName) : 'overview'
  })
  const [overview, setOverview] = useState<Overview | null>(null)
  const [toasts, setToasts] = useState<Toast[]>([])
  const [drawer, setDrawer] = useState(false)
  const [unread, setUnread] = useState(0)
  const [guideKey, setGuideKey] = useState(0)
  const activeIdRef = useRef<string | null>(null)
  const toastSeq = useRef(0)

  const toast = useCallback((level: string, message: string) => {
    if (!message) return
    const id = ++toastSeq.current
    setToasts((prev) => [...prev.slice(-3), { id, level, message }])
    setUnread((n) => n + 1)
    window.setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 5200)
  }, [])

  const reload = useCallback(() => {
    api.overview().then(setOverview).catch((e: Error) => toast('ERROR', `数据刷新失败：${e.message}`))
  }, [toast])

  const onNotice = useCallback((level: string, message: string) => {
    if (level === 'tick') return  // 轮询心跳不打扰用户
    toast(level === 'error' ? 'ERROR' : level === 'switch' ? 'ROUTE' : 'INFO', message)
    reload()
  }, [toast, reload])

  const staticMode = new URLSearchParams(location.search).has('nosse')
  const { stages, resetStages, connected } = useEventStream(activeIdRef, onNotice, !staticMode)

  useEffect(() => { reload() }, [reload])
  useEffect(() => {
    const timer = window.setInterval(reload, 6000)
    return () => window.clearInterval(timer)
  }, [reload])
  useEffect(() => {
    const onHash = () => {
      const h = location.hash.replace('#', '')
      const valid: ViewName[] = ['landing', 'guide', 'overview', 'console', 'chat', 'team']
      setView(valid.includes(h as ViewName) ? (h as ViewName) : 'overview')
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const go = (v: ViewName) => { location.hash = v; setView(v); setUnread(0) }

  const viewProps: ViewProps | null = overview ? {
    overview, reload, toast, stages, activeIdRef, resetStages,
    openDrawer: () => setDrawer(true),
  } : null

  return (
    <div className="app">
      <aside className="side">
        <div className="brand"><div className="logo">S</div><div><b>SafeShell Pro</b><small>安全 AI 运维命令栈</small></div></div>
        {NAV.map((g) => (
          <div key={g.group}>
            <div className="grp">{g.group}</div>
            {g.items.map((it) => (
              <button key={it.id} className={`nav ${view === it.id ? 'on' : ''}`} onClick={() => go(it.id)}>
                <i className="ic" /><span>{it.label}</span>
              </button>
            ))}
          </div>
        ))}
        <div className="grp">CONFIG &amp; ALERT</div>
        <button className="nav" onClick={() => setDrawer(true)}><i className="ic" /><span>发现免费 API</span></button>
        <button className="nav" onClick={() => go('overview')}><i className="ic" /><span>切换策略</span></button>
        <button className="nav" onClick={() => toast('INFO', '告警规则：阈值与渠道在「切换策略」卡片中配置')}>
          <i className="ic" /><span>告警规则</span></button>
        <div className="foot">
          <span className={`dot ${connected ? 'green' : 'red'}`} />DATA SOURCE · {overview?.demo_mode ? 'MOCK' : 'LIVE'}<br />
          <span style={{ marginLeft: 13 }}>v0.2.0 · 6s 轮询</span>
        </div>
      </aside>

      <div className="main">
        <div className="top">
          <div className="crumb">工作区<i>/</i>{view === 'landing' ? '产品与定价' : view === 'guide' ? '新手接入向导' : view === 'overview' ? '额度总览' : view === 'console' ? '调用控制台' : view === 'chat' ? '对话测试台' : '团队控制台'}</div>
          <span className={`badge ${overview?.demo_mode === false ? 'live' : ''}`}>
            {overview?.demo_mode === false ? 'LIVE' : 'DEMO'}
          </span>
          <div className={`sync ${connected ? '' : 'off'}`}>● {staticMode ? '静态校验模式' : connected ? 'SSE 实时同步' : 'SSE 连接中断'}</div>
          <button className="bell" onClick={() => { setUnread(0); go('overview') }} aria-label="告警通知">
            ◔ {unread > 0 && <i />}
          </button>
        </div>

        {view !== 'guide' && view !== 'landing' && view !== 'team' && (
          <GuideBanner onOpen={() => go('guide')} refreshKey={guideKey} />
        )}

        {view === 'landing' ? (
          <Landing onEnter={(v) => { location.hash = v; setView(v as ViewName) }} />
        ) : view === 'guide' ? (
          <Onboarding onGoChat={() => go('chat')} toast={toast}
            onChanged={() => { reload(); setGuideKey((k) => k + 1) }} />
        ) : view === 'team' ? (
          <TeamConsole toast={toast} />
        ) : !viewProps ? (
          <div className="view"><div className="empty">正在连接服务端…若长时间无响应，请确认 uvicorn 已在 8772 端口启动。</div></div>
        ) : view === 'overview' ? <OverviewView {...viewProps} /> :
          view === 'console' ? <ConsoleView {...viewProps} /> : <ChatView {...viewProps} />}
      </div>

      <div className={`toasts ${drawer ? 'shift' : ''}`}>
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.level}`}>
            <span className="lv">{t.level} · {new Date().toLocaleTimeString('zh-CN', { hour12: false })}</span>
            {t.message}
          </div>
        ))}
      </div>

      {drawer && <DiscoverDrawer onClose={() => setDrawer(false)} reload={reload} toast={toast} />}
    </div>
  )
}
