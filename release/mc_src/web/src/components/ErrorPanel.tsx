import type { ChatResponse } from '../api'
import { api } from '../api'
import { errorUI } from '../stageText'

interface Props {
  result: ChatResponse
  toast: (level: string, message: string) => void
  onRetry: () => void
  onEdit: () => void
}

export default function ErrorPanel({ result, toast, onRetry, onEdit }: Props) {
  const ui = errorUI(result.error_code)
  const act = async (action: string) => {
    if (action === 'retry') { onRetry(); return }
    if (action === 'edit') { onEdit(); return }
    if (action === 'feedback') {
      try {
        await api.feedback(result.request_id, 'down', `${result.error_code} · ${ui.title}`)
        toast('INFO', '反馈已写入事件流与结构化日志')
      } catch (e) { toast('ERROR', `反馈失败：${(e as Error).message}`) }
      return
    }
    if (action === 'docs') {
      const url = result.sources[0]?.url || 'https://docs.local/demo'
      window.open(url, '_blank', 'noopener,noreferrer')
    }
  }
  return (
    <div className={`errbox ${ui.scenario === 'hallucination' ? 'amber' : ''}`} role="alert">
      <div className="ttl">{ui.title}</div>
      <div className="desc">{ui.desc}</div>
      <div className="code">error_code: {result.error_code} · request_id: {result.request_id} · stage: {result.stages.length ? result.stages[result.stages.length - 1].stage : 'parse'}</div>
      <div className="acts">
        {ui.actions.map((a) => <button key={a.action} className={`btn sm ${a.action === 'retry' ? 'primary' : ''}`} onClick={() => act(a.action)}>{a.label}</button>)}
      </div>
    </div>
  )
}
