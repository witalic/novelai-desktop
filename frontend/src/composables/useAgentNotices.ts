/* What agents change, as it happens (backend app/agent/changes, GET /api/agent/events): one event stream for the
 * whole app, fanned out to whoever listens — the app toasts each notice; the Library, the Works list and an open
 * work refresh. The browser reconnects a dropped stream by itself; a notice missed meanwhile costs an open work
 * nothing (its next save meets a 409 and replays the agent's edits then). */
import { onUnmounted } from 'vue'

export interface AgentNotice {
  kind: 'library' | 'work' | 'failed' // what changed: Library blocks, the work `work_id` — or a generation failed
  work_id: string | null
  message: string // what the agent did, as the activity feed words it
  cost: number | null // a generation's Anlas (0 = free); null for everything else
}
type Listener = (notice: AgentNotice) => void

const RETRY_MS = 5000
const listeners = new Set<Listener>()
let source: EventSource | null = null

function connect() {
  if (source || typeof EventSource === 'undefined') return
  source = new EventSource('/api/agent/events')
  // The browser retries a dropped connection itself, but gives up for good on an error response — start over.
  source.onerror = () => {
    if (source?.readyState !== EventSource.CLOSED) return
    source = null
    setTimeout(connect, RETRY_MS)
  }
  source.onmessage = (ev) => {
    let notice: AgentNotice
    try { notice = JSON.parse(ev.data) } catch { return }
    for (const fn of [...listeners]) {
      try { fn(notice) } catch { /* one listener's failure never starves the rest */ }
    }
  }
}

/** Hear every agent notice for as long as the calling component lives (KeepAlive'd views keep hearing them). */
export function onAgentNotice(fn: Listener): void {
  connect()
  listeners.add(fn)
  onUnmounted(() => listeners.delete(fn))
}
