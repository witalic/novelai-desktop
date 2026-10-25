/* Agent activity, as Settings → Agent activity shows it: the filters (by kind, or what went wrong) and the day
 * groups, so an app left running past midnight doesn't run two days together. */
import type { AgentActivity } from './api'

export type ActivityFilter = 'all' | 'write' | 'generate' | 'read' | 'problem'
export const ACTIVITY_FILTERS: { id: ActivityFilter; label: string }[] = [
  { id: 'all', label: 'All' }, { id: 'write', label: 'Changes' }, { id: 'generate', label: 'Generations' },
  { id: 'read', label: 'Reads' }, { id: 'problem', label: 'Refused & failed' },
]

/** A refused or failed call is only a problem — it doesn't also count as a change, read or generation. */
export function matchesFilter(a: AgentActivity, f: ActivityFilter): boolean {
  return f === 'all' || (f === 'problem' ? a.outcome !== 'ok' : a.kind === f && a.outcome === 'ok')
}

export function dayLabel(iso: string, now = new Date()): string {
  const d = new Date(iso)
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1)
  if (d.toDateString() === now.toDateString()) return 'Today'
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday'
  return d.toLocaleDateString([], { day: 'numeric', month: 'short' })
}

/** Consecutive rows of one day under one header (rows come newest first). */
export function groupByDay(rows: AgentActivity[], now = new Date()): { day: string; rows: AgentActivity[] }[] {
  const out: { day: string; rows: AgentActivity[] }[] = []
  for (const a of rows) {
    const day = dayLabel(a.at, now)
    let last = out[out.length - 1]
    if (last?.day !== day) out.push(last = { day, rows: [] })
    last.rows.push(a)
  }
  return out
}
