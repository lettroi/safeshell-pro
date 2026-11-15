import { useCallback, useEffect, useState } from 'react'
import type { ChatResponse, ConnectPreset, ConnectStatus, ConnectTestResult } from '../api'
import { api } from '../api'

interface GuideProps {
  onGoChat: () => void
  toast: (level: string, message: string) => void
  onChanged: () => void
}

const STEP_META = [
  { title: '① 先看懂当前模式', desc: '分清哪些是演示数据、哪些是真的' },
  { title: '② 选一家免费平台', desc: '按官方链接注册并领 API Key' },
  { title: '③ 填 Key 并测试连接', desc: '验证通过后保存，工作台自动启用' },
  { title: '④ 发一条真实消息', desc: '看到真实回复就说明接好了' },
]

export function GuideBanner({ onOpen, refreshKey }: { onOpen: () => void; refreshKey: number }) {
  const [status, setStatus] = useState<ConnectStatus[]>([])
  useEffect(() => {
    api.connectStatus().then(setStatus).catch(() => { /* 后端未就绪时显示演示态 */ })
  }, [refreshKey])
  const live = status.filter((s) => s.configured)
  return (
    <div className={`guide-banner ${live.length ? 'live' : ''}`}>
      <div className="gb-main">
        <div className="gb-tag">{live.length ? '已接入真实模型' : '当前：演示模式'}</div>
        <div className="gb-title">
          {live.length
            ? `已连接 ${live.length} 家：${live.map((x) => x.name).join('、')}`
            : '现在看到的额度与回答都是本地模拟数据'}
        </div>
        <div className="gb-desc">
          {live.length
            ? '对话测试台已自动切换到真实模型。额度表仍是演示数据，真实用量与账单请到厂商控制台查看。'
            : '不会调用任何真实模型、不需要密钥。想用真实免费大模型，按向导做四步：选平台 → 领 Key → 测试连接 → 发消息。'}
        </div>
      </div>
      <div className="gb-act">
        <button className="btn primary" onClick={onOpen}>
          {live.length ? '查看教程 / 管理接入' : '3 分钟接入真实免费模型 →'}
        </button>
        {!live.length && <span className="small">不接入也能体验全部界面</span>}
      </div>
    </div>
  )
}

export default function Onboarding({ onGoChat, toast, onChanged }: GuideProps) {
  const [step, setStep] = useState(0)
  const [presets, setPresets] = useState<ConnectPreset[]>([])
  const [showMore, setShowMore] = useState(false)
  const [selected, setSelected] = useState<ConnectPreset | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [apiBase, setApiBase] = useState('')
  const [model, setModel] = useState('')
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<ConnectTestResult | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [probe, setProbe] = useState('用一句话介绍你自己')
  const [probing, setProbing] = useState(false)
  const [probeResult, setProbeResult] = useState<ChatResponse | null>(null)
  const [probeError, setProbeError] = useState('')

  const load = useCallback(() => {
    api.connectPresets().then(setPresets).catch((e: Error) =>
      toast('ERROR', `加载平台清单失败：${e.message}`))
  }, [toast])
  useEffect(() => { load() }, [load])

  const choose = (p: ConnectPreset) => {
    setSelected(p); setApiBase(p.api_base); setModel(p.model)
    setApiKey(''); setTestResult(null); setSaved(false)
    setProbeResult(null); setProbeError('')
    setStep(2)
  }

  const runTest = async () => {
    if (!selected) return
    setTesting(true); setTestResult(null)
    try {
      const r = await api.connectTest({
        provider_id: selected.id, api_key: apiKey, api_base: apiBase, model,
      })
      setTestResult(r)
    } catch (e) {
      setTestResult({ ok: false, latency_ms: 0, model, error_code: 'UPSTREAM_ERROR',
        message: (e as Error).message, hint: '检查服务端是否已启动，或稍后重试。', preview: '' })
    } finally { setTesting(false) }
  }

  const save = async () => {
    if (!selected) return
    setSaving(true)
    try {
      await api.connectConfigure({
        provider_id: selected.id, api_key: apiKey, api_base: apiBase, model,
      })
      setSaved(true)
      toast('ROUTE', `已启用 ${selected.name}，对话现在走真实模型`)
      load(); onChanged()
    } catch (e) { toast('ERROR', `保存失败：${(e as Error).message}`) }
    finally { setSaving(false) }
  }

  const doRemove = async () => {
    if (!selected) return
    try {
      await api.connectRemove(selected.id)
      toast('INFO', '已移除接入配置，回到演示模式')
      load(); onChanged(); setSaved(false); setTestResult(null)
    } catch (e) { toast('ERROR', `移除失败：${(e as Error).message}`) }
  }

  const runProbe = async () => {
    if (!selected) return
    setProbing(true); setProbeResult(null); setProbeError('')
    try {
      const r = await api.chat({ user_id: 'guide_user', message: probe, provider_id: selected.id })
      setProbeResult(r)
      if (r.error_code) setProbeError(`error_code: ${r.error_code} · ${r.answer}`)
    } catch (e) { setProbeError(`请求失败：${(e as Error).message}`) }
    finally { setProbing(false) }
  }

  const visible = presets.filter((p) => showMore || p.recommended)
  const configuredCount = presets.filter((p) => p.configured).length

  return (
    <div className="view">
      <div className="title-row">
        <div>
          <h1>新手接入向导</h1>
          <div className="sub">四步从「全是演示数据」到「真实免费大模型回答」。全程可跳过，不接入也能体验全部界面。</div>
        </div>
      </div>

      <div className="guide-steps-bar">
        {STEP_META.map((m, i) => (
          <button key={i} className={`gstep ${step === i ? 'on' : ''} ${step > i ? 'done' : ''}`}
            onClick={() => { if (i <= step) setStep(i) }}>
            <b>{m.title}</b><span>{m.desc}</span>
          </button>
        ))}
      </div>

      {step === 0 && (
        <div className="card">
          <div className="head">现在你在看什么</div>
          <div className="body">
            <div className="gm-grid">
              <div>
                <b className="gm-tag amber">演示模式（默认）</b>
                <ul>
                  <li>9 个数据源的额度、剩余百分比、24h 趋势：本地模拟，轮询时固定增加</li>
                  <li>对话回答：由本地政策清单拼装，不调用任何模型</li>
                  <li>429 演练、自动切换、结构化日志：完整可跑，用来理解工作流程</li>
                  <li>零配置、不花钱、不联网，适合先练手</li>
                </ul>
              </div>
              <div>
                <b className="gm-tag green">接入后（真实模式）</b>
                <ul>
                  <li>对话回答来自你选的厂商，顶栏显示 LIVE 与真实模型名</li>
                  <li>免费额度、限流、账单以厂商控制台为准</li>
                  <li>额度表 / 趋势仍是演示数据，本版不读取厂商 Usage</li>
                  <li>需要你自己申请 API Key，本工具不代注册、不上传密钥</li>
                </ul>
              </div>
            </div>
            <div className="alert amber" style={{ marginTop: 14, marginBottom: 0 }}>
              <div className="txt">
                <b>关于「免费」</b>：政策变化很快，内置清单是 2026-11-13 的官方核实快照，
                部分平台当年已回收免费层。注册前请点向导里的官方链接再确认一次；
                「送多少 token」的一次性赠金和「长期免费层」不是一回事。
              </div>
            </div>
            <div className="hr" />
            <div className="inline">
              <button className="btn primary" onClick={() => setStep(1)}>开始：选一家免费平台 →</button>
              <button className="btn" onClick={onGoChat}>先去随便看看界面</button>
            </div>
          </div>
        </div>
      )}

      {step === 1 && (
        <>
          <div className="sub" style={{ marginBottom: 12 }}>
            下面每家都给了「注册 → 领 Key → 官方政策」三个直达链接。国内网络优先选国内平台。
            已接入 {configuredCount} 家。
          </div>
          <div className="guide-cards">
            {visible.map((p) => (
              <div className={`guide-card ${p.recommended ? 'rec' : ''}`} key={p.id}>
                <div className="gc-head">
                  <b>{p.name}</b><span className="chip">{p.region}</span>
                  {p.configured && <span className="chip on">已接入</span>}
                </div>
                <p className="gc-policy">{p.free_policy}</p>
                <div className="gc-links">
                  <a href={p.signup_url} target="_blank" rel="noreferrer">① 注册账号 ↗</a>
                  <a href={p.key_url} target="_blank" rel="noreferrer">② 打开 API Key 页 ↗</a>
                  <a href={p.docs_url} target="_blank" rel="noreferrer">③ 官方免费政策 ↗</a>
                </div>
                <div className="gc-foot">
                  <span className="small">{p.notes}</span>
                  <button className="btn primary sm" onClick={() => choose(p)}>选这家 →</button>
                </div>
              </div>
            ))}
          </div>
          <div className="inline" style={{ marginTop: 10 }}>
            <button className="btn ghost" onClick={() => setShowMore((v) => !v)}>
              {showMore ? '收起更多平台' : `显示全部 ${presets.length} 家平台`}
            </button>
            <span className="small">不确定选哪家？推荐智谱（国内）或 Groq（海外），注册最简单。</span>
          </div>
        </>
      )}

      {step === 2 && selected && (
        <div className="card">
          <div className="head">接入 {selected.name}<small>{selected.region}</small></div>
          <div className="body">
            <div className="guide-todo">
              {selected.steps.map((s, i) => (
                <div key={i}><span className="gnum">{i + 1}</span><span>{s}</span></div>
              ))}
            </div>
            <div className="inline" style={{ marginBottom: 12 }}>
              <a href={selected.key_url} target="_blank" rel="noreferrer">打开官方 Key 页 ↗</a>
              <span className="small">·</span>
              <a href={selected.docs_url} target="_blank" rel="noreferrer">查看官方文档 ↗</a>
              <span className="small">· 拿不到 Key 就先完成上面的注册步骤</span>
            </div>
            <div className="hr" />
            <div className="field">
              <label>API Key（只存本机服务端；页面只显示脱敏值，日志不记录明文）</label>
              <input type="password" value={apiKey} placeholder="粘贴从官方控制台复制的 Key"
                onChange={(e) => setApiKey(e.target.value)} />
            </div>
            <div className="field">
              <label>接口地址 base_url（可编辑；各厂商路径不同，以官方文档为准）</label>
              <input value={apiBase} onChange={(e) => setApiBase(e.target.value)} />
            </div>
            <div className="field">
              <label>模型名（可编辑；免费模型会换代下架，以官方模型列表为准）</label>
              <input value={model} placeholder={selected.model_help || '例如 GLM-4.7-Flash'}
                onChange={(e) => setModel(e.target.value)} />
            </div>
            {selected.model_help && <div className="small" style={{ marginBottom: 10 }}>{selected.model_help}</div>}
            <div className="inline">
              <button className="btn" onClick={runTest} disabled={testing}>
                {testing ? '正在测试…' : '① 测试连接'}
              </button>
              <button className="btn primary" onClick={save} disabled={!testResult?.ok || saving}>
                {saving ? '保存中…' : '② 保存并启用'}
              </button>
              <button className="btn ghost" onClick={() => { setStep(1); setSelected(null) }}>换一家</button>
            </div>
            {testResult && (
              <div className={`guide-result ${testResult.ok ? 'ok' : 'bad'}`}>
                <b>{testResult.ok ? '连接成功' : `连接失败：${testResult.message}`}</b>
                {testResult.preview && <div className="small">模型回复：{testResult.preview}</div>}
                <div>{testResult.hint}</div>
                {testResult.ok && (
                  <div className="small">延迟 {testResult.latency_ms} ms · 模型 {testResult.model}</div>
                )}
              </div>
            )}
            {saved && (
              <div className="guide-result ok">
                <b>已保存并启用，整个工作台现在走真实模型</b>
                <div>下一步发一条真实消息验证一下。</div>
                <button className="btn primary sm" style={{ marginTop: 8 }} onClick={() => setStep(3)}>
                  去发一条真实消息 →
                </button>
              </div>
            )}
            {selected.configured && (
              <div className="inline" style={{ marginTop: 10 }}>
                <span className="small">当前已保存的密钥：{selected.key_masked}</span>
                <button className="btn danger sm" onClick={doRemove}>移除配置，回到演示模式</button>
              </div>
            )}
            <div className="foot-note">
              测试连接会向该厂商发一条极小的真实请求（约 16 tokens）。密钥只保存在本机 SQLite，
              不会写入日志、不会上传；共用电脑请谨慎。
            </div>
          </div>
        </div>
      )}

      {step === 3 && selected && (
        <div className="card">
          <div className="head">用 {selected.name} 发一条真实消息</div>
          <div className="body">
            <div className="sub" style={{ marginBottom: 12 }}>
              这次请求会真的走你刚接入的模型。如果还没保存配置，请先回到第 ③ 步测试并保存。
            </div>
            <div className="field">
              <label>说点什么</label>
              <input value={probe} onChange={(e) => setProbe(e.target.value)} />
            </div>
            <button className="btn primary" onClick={runProbe} disabled={probing}>
              {probing ? '请求中…' : '发送真实请求'}
            </button>
            {probeError && <div className="guide-result bad"><b>{probeError}</b><div>回到第 ③ 步检查 Key、端点与模型名。</div></div>}
            {probeResult && !probeResult.error_code && (
              <div className="guide-result ok">
                <b>收到真实回答 · {probeResult.model} · {probeResult.latency_ms} ms · {probeResult.token_usage} tokens</b>
                <div className="gr-answer">{probeResult.answer}</div>
                {probeResult.sources.length > 0 && (
                  <div className="small">参考材料：{probeResult.sources.map((s) => s.title).join('、')}</div>
                )}
              </div>
            )}
            <div className="hr" />
            <div className="guide-next">
              <b>接好了，接下来可以用</b>
              <ul>
                <li>「对话测试台」继续聊——保存配置后已自动切到真实模型，无需再选</li>
                <li>「额度总览 / 调用控制台」看切换策略、429 演练与结构化日志（这部分是演示数据）</li>
                <li>厂商控制台查真实剩余额度、限流与账单；免费额度用完会自动按量付费，建议开启「用完即停」</li>
                <li>想换平台：回到第 ② 步选一家，或在本页移除配置回到演示模式</li>
              </ul>
              <button className="btn primary" onClick={onGoChat}>去对话测试台 →</button>
            </div>
          </div>
        </div>
      )}

      {step > 0 && (
        <div className="guide-nav">
          <button className="btn" onClick={() => setStep((s) => s - 1)}>← 上一步</button>
          {step === 1 && <span className="small">选一家后会自动进入填写页</span>}
          {step === 3 && <button className="btn" onClick={onGoChat}>跳过验证，直接去对话 →</button>}
        </div>
      )}
    </div>
  )
}
