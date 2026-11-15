import type { Stage } from '../api'
import { STAGE_ORDER, STAGE_TEXT } from '../stageText'

export default function StagesPanel({ stages }: { stages: Stage[] }) {
  const current = stages.length ? stages[stages.length - 1].stage : 'parse'
  return (
    <div className="stages" aria-live="polite" aria-label="请求处理阶段">
      <div className="small" style={{ marginBottom: 8 }}>SSE /api/v1/events/stream · 后端真实阶段（非定时模拟）</div>
      {STAGE_ORDER.map((s, i) => {
        const item = stages.find((x) => x.stage === s)
        const cls = item ? (s === current ? 'now' : 'done') : ''
        return (
          <div className={`stage ${cls}`} key={s}>
            <span className="n">{item && s !== current ? '✓' : i + 1}</span>
            <span className="t">{STAGE_TEXT[s].label}<div className="small">{STAGE_TEXT[s].hint}</div></span>
            <span className="code">stage: {s}</span>
            <span className="ms">{item ? `${item.elapsed_ms} ms` : '—'}</span>
          </div>
        )
      })}
      {stages.some((s) => s.elapsed_ms > 8000) && <div className="small" style={{ marginTop: 8 }}>仍在生成，可继续等待…</div>}
    </div>
  )
}
