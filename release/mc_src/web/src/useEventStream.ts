import { useCallback, useEffect, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import type { SSEEvent, Stage } from './api'

export interface Toast { id: number; level: string; message: string }

export function useEventStream(
  activeIdRef: MutableRefObject<string | null>,
  onNotice: (level: string, message: string) => void,
  enabled = true,
) {
  const [stages, setStages] = useState<Stage[]>([])
  const [connected, setConnected] = useState(false)
  const cbRef = useRef(onNotice)
  cbRef.current = onNotice

  const resetStages = useCallback(() => setStages([]), [])

  useEffect(() => {
    // ?nosse=1 供无头浏览器静态截图使用：关闭长连接避免进程挂起，真实运行不受影响
    if (!enabled) { setConnected(true); return }
    const es = new EventSource('/api/v1/events/stream')
    es.addEventListener('connected', () => setConnected(true))
    es.addEventListener('stage', (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data) as SSEEvent
        if (data.event !== 'stage') return
        const stage = data as Extract<SSEEvent, { event: 'stage' }>
        const active = activeIdRef.current
        if (active && stage.request_id !== active) return
        setStages((prev) => [...prev.filter((s) => s.stage !== stage.stage),
          { stage: stage.stage, elapsed_ms: stage.elapsed_ms, detail: stage.detail }])
      } catch { /* 忽略畸形事件 */ }
    })
    es.addEventListener('notification', (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data) as SSEEvent
        if (data.event === 'notification') {
          cbRef.current(data.level || 'INFO', data.message || '')
        }
      } catch { /* 忽略畸形事件 */ }
    })
    es.onerror = () => setConnected(false)
    return () => { es.close(); setConnected(false) }
  }, [activeIdRef, enabled])

  return { stages, resetStages, connected }
}
