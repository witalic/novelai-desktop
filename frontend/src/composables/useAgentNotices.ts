/* What agents change, as it happens (backend app/agent/changes, GET /api/agent/events): one event stream for the
 * whole app, fanned out to whoever listens — the app toasts each notice; the Library, the Works list and an open
 * work refresh. The browser reconnects a dropped stream by itself; a notice missed meanwhile costs an open work
 * nothing (its next save meets a 409 and replays the agent's edits then). */
import { onUnmounted } from 'vue'

export interface AgentNotice {
  // what changed: Library blocks, the work `work_id` (deleted, for `work_deleted`) — or a generation failed
  kind: 'library' | 'work' | 'work_deleted' | 'failed'
  work_id: string | null
  also?: string[] // other works the same edit changed (images moved out of one into another)
  message: string // what the agent did, as the activity feed words it
  cost: number | null // a generation's Anlas (0 = free); null for everything else
  local?: boolean // the app's own change, told the same way (announceWorkDeleted, the gallery's edits) — no toast
}
type Listener = (notice: AgentNotice) => void

/** Whether a notice says the agent edited this work (it was the one changed, or one of them). */
export function editedWork(n: AgentNotice, workId: string | null | undefined): boolean {
  return n.kind === 'work' && !!workId && (n.work_id === workId || !!n.also?.includes(workId))
}

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
    tell(notice)
  }
}

function tell(notice: AgentNotice) {
  for (const fn of [...listeners]) {
    try { fn(notice) } catch { /* one listener's failure never starves the rest */ }
  }
}

/** A work the user deleted (the Works list), told to the listeners an agent's deletion reaches — so an open copy of it
 *  lets it go then, rather than at its next save. */
export function announceWorkDeleted(workId: string): void {
  tell({ kind: 'work_deleted', work_id: workId, message: '', cost: null, local: true })
}

/** Hear every agent notice for as long as the calling component lives (KeepAlive'd views keep hearing them). */
export function onAgentNotice(fn: Listener): void {
  connect()
  listeners.add(fn)
  onUnmounted(() => listeners.delete(fn))
}
