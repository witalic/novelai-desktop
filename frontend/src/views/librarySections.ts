import type { LibraryBlock } from '../types'

export interface Section {
  slug: string
  items: LibraryBlock[]
}

// Group a `sort=category` block page (blocks arrive category-contiguous, so equal categories are
// adjacent even across accumulated pages) into one section per consecutive run. Not a global
// group-by: it merges only neighbouring blocks, which is exactly what infinite-scroll accumulation
// produces and keeps a section from splitting when a later page continues the same category.
export function groupByCategory(blocks: LibraryBlock[]): Section[] {
  const out: Section[] = []
  for (const b of blocks) {
    const last = out[out.length - 1]
    if (last && last.slug === b.category) last.items.push(b)
    else out.push({ slug: b.category, items: [b] })
  }
  return out
}
