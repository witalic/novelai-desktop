/* Deduplicate repeated tags in a NovelAI prompt (framework-free, unit-tested).
 *
 * The same tag appearing in several blocks is redundant; NovelAI treats repetition as emphasis (the
 * weights stack), so instead of dropping duplicates we MERGE them and SUM their weights — preserving
 * the effect while cutting tokens. A plain tag has weight 1.0; `1.3::tag::` has weight 1.3. A tag that
 * appears once is left exactly as-is (no-op). Merged weights are capped so incidental duplication in a
 * few library blocks can't blow up into an extreme emphasis. Order = first occurrence.
 */
const MAX_WEIGHT = 1.5 // ceiling for a MERGED tag — tune here if it should allow stronger stacking

interface Tag { display: string; weight: number }

// Parse a prompt into (display, weight) units. A weighted group `N::content::` is atomic (its content
// may hold commas); everything else is split on commas into plain, weight-1 tags.
function parseTags(text: string): Tag[] {
  const tags: Tag[] = []
  const pushPlain = (s: string) => {
    for (const part of s.split(',')) {
      const t = part.trim()
      if (t) tags.push({ display: t, weight: 1 })
    }
  }
  const re = /(-?\d+(?:\.\d+)?)::(.*?)::/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    pushPlain(text.slice(last, m.index))
    const display = m[2].trim()
    if (display) tags.push({ display, weight: Number.parseFloat(m[1]) })
    last = re.lastIndex
  }
  pushPlain(text.slice(last))
  return tags
}

const fmtWeight = (w: number) => String(Math.round(w * 100) / 100)

export function dedupePrompt(text: string, cap = MAX_WEIGHT): string {
  const order: string[] = []
  const merged = new Map<string, { display: string; weight: number; count: number }>()
  for (const t of parseTags(text)) {
    const key = t.display.toLowerCase()
    const ex = merged.get(key)
    if (ex) { ex.weight += t.weight; ex.count += 1 }
    else { merged.set(key, { display: t.display, weight: t.weight, count: 1 }); order.push(key) }
  }
  return order.map((key) => {
    const { display, weight, count } = merged.get(key)!
    // A tag seen once keeps its exact weight (never capped); only actual merges sum + cap.
    const w = count === 1 ? weight : Math.min(cap, weight)
    return Math.abs(w - 1) < 1e-9 ? display : `${fmtWeight(w)}::${display}::`
  }).join(', ')
}
