/* The Gallery's filters (design/global-gallery.html) and how they become a query (backend /api/vault/gallery). A
 * period is counted in the viewer's own days — "today" starts at their midnight — and sent as moments with their
 * offset, never as bare dates. */

export type Period =
  | { kind: 'any' } | { kind: 'today' } | { kind: 'week' } | { kind: 'month' }
  | { kind: 'range'; from: string; to: string } // local days, both included: 'YYYY-MM-DD'

export interface GalleryFilters {
  q: string
  tags: string[]
  works: string[]
  models: string[]
  favorite: boolean
  period: Period
  unsorted: boolean // images in no album (a work's Quick access) included
  order: 'newest' | 'oldest'
}

export const NO_FILTERS: GalleryFilters = {
  q: '', tags: [], works: [], models: [], favorite: false, period: { kind: 'any' }, unsorted: false, order: 'newest',
}

const DAY = 24 * 60 * 60 * 1000
export const UI_LOCALE = 'en-GB' // dates in the app's own language, whatever the system's

function midnight(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}

/** A local day 'YYYY-MM-DD' as its midnight. */
export function dayStart(day: string): Date {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(y, m - 1, d)
}

/** 'YYYY-MM-DD' of a local date. */
export function dayOf(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** The moments a period spans: from its first midnight, up to (not including) the midnight after its last day. */
export function periodBounds(p: Period, now: Date = new Date()): { since?: Date; until?: Date } {
  const today = midnight(now)
  const back = (days: number) => new Date(today.getFullYear(), today.getMonth(), today.getDate() - days)
  switch (p.kind) {
    case 'today': return { since: today }
    case 'week': return { since: back(6) } // today and the six days before it
    case 'month': return { since: back(29) }
    case 'range': {
      const last = dayStart(p.to)
      return { since: dayStart(p.from), until: new Date(last.getFullYear(), last.getMonth(), last.getDate() + 1) }
    }
    default: return {}
  }
}

export function periodLabel(p: Period): string {
  switch (p.kind) {
    case 'any': return 'Any time'
    case 'today': return 'Today'
    case 'week': return 'Last 7 days'
    case 'month': return 'Last 30 days'
    case 'range': {
      const f = (day: string) => dayStart(day).toLocaleDateString(UI_LOCALE, { day: 'numeric', month: 'short' })
      return p.from === p.to ? f(p.from) : `${f(p.from)} – ${f(p.to)}`
    }
  }
}

/** The query the filters make — what the list and its facets are asked for. */
export function galleryParams(f: GalleryFilters, now: Date = new Date()): URLSearchParams {
  const p = new URLSearchParams()
  if (f.q.trim()) p.set('q', f.q.trim())
  for (const t of f.tags) p.append('tags', t)
  for (const w of f.works) p.append('work', w)
  for (const m of f.models) p.append('model', m)
  if (f.favorite) p.set('favorite', 'true')
  const { since, until } = periodBounds(f.period, now)
  if (since) p.set('since', since.toISOString())
  if (until) p.set('until', until.toISOString())
  p.set('unsorted', f.unsorted ? 'include' : 'exclude')
  return p
}

/** The day heading an image sits under: Today, Yesterday, a weekday this week, else the date. */
export function dayHeading(createdAt: string, now: Date = new Date()): string {
  const at = new Date(createdAt)
  if (Number.isNaN(at.getTime())) return 'Undated'
  const ago = Math.round((midnight(now).getTime() - midnight(at).getTime()) / DAY)
  if (ago === 0) return 'Today'
  if (ago === 1) return 'Yesterday'
  const sameYear = at.getFullYear() === now.getFullYear()
  return at.toLocaleDateString(UI_LOCALE, ago > 1 && ago < 7
    ? { weekday: 'long' }
    : { weekday: 'short', day: 'numeric', month: 'short', ...(sameYear ? {} : { year: 'numeric' }) })
}
