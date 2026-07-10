/* Single-column "magnetic" packing for the prompt-widget palette (framework-free, unit-testable).
 * The palette's layout IS its order: children pack top-to-bottom in palette order, and a drop
 * picks its slot by y — there is no free placement inside the widget. */

export interface PackChild {
  id: string
  h: number
}

export const PACK_TOP = 50 // first row sits below the widget header
export const PACK_GAP = 8
export const PACK_X = 12
const PACK_BOTTOM = 44 // room for the widget footer under the last row

// Column positions for `items` in palette order.
export function packColumn(items: PackChild[]): { id: string; x: number; y: number }[] {
  let y = PACK_TOP
  return items.map((it) => {
    const pos = { id: it.id, x: PACK_X, y }
    y += it.h + PACK_GAP
    return pos
  })
}

// Zone height needed for the whole column (auto-grow target).
export function packedHeight(items: PackChild[]): number {
  if (!items.length) return PACK_TOP + PACK_BOTTOM
  return items.reduce((y, it) => y + it.h + PACK_GAP, PACK_TOP) - PACK_GAP + PACK_BOTTOM
}

// Insertion slot for a drop at zone-relative `dropY`: before the first row whose midline lies
// below the drop point. Above everything → 0, below everything → items.length.
export function insertionIndex(rows: { y: number; h: number }[], dropY: number): number {
  const i = rows.findIndex((r) => dropY < r.y + r.h / 2)
  return i === -1 ? rows.length : i
}
