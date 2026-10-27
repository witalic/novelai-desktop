import type { WorkMatch } from '../types'

export interface Part { text: string; hit: boolean }

const LABEL: Record<Exclude<WorkMatch['kind'], 'images'>, string> = { section: 'Section', heading: 'Heading', note: 'Note' }

// `text` in pieces, each found `query` marked — rendered as text nodes, never as HTML.
export function highlight(text: string, query: string): Part[] {
  const q = query.trim().toLocaleLowerCase()
  if (!q) return [{ text, hit: false }]
  const lower = text.toLocaleLowerCase()
  const parts: Part[] = []
  let at = 0
  for (let i = lower.indexOf(q); i >= 0; i = lower.indexOf(q, at)) {
    if (i > at) parts.push({ text: text.slice(at, i), hit: false })
    parts.push({ text: text.slice(i, i + q.length), hit: true })
    at = i + q.length
  }
  if (at < text.length) parts.push({ text: text.slice(at), hit: false })
  return parts
}

// Where a work matched a search its title didn't, said on its card: “moon” in 31 images · Section “Harvest moon”.
export function matchParts(match: WorkMatch, query: string): Part[] {
  if (match.kind === 'images') {
    const n = match.count
    return [{ text: '“', hit: false }, { text: query.trim(), hit: true },
      { text: `” in ${n} image${n === 1 ? '' : 's'}`, hit: false }]
  }
  return [{ text: `${LABEL[match.kind]} “`, hit: false }, ...highlight(match.text ?? '', query), { text: '”', hit: false }]
}
