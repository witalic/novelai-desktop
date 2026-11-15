/* The Gallery's grid (design/global-gallery.html): images keep their true shape in justified rows — each full row
 * stretched to the width, the last row of a day left at the target height — under a heading per day. Pure layout:
 * every row's place, so the grid draws only the rows on screen. */

export interface LayoutItem { id: string; ar: number; day: string } // ar = width / height
export interface Tile { id: string; x: number; w: number }
export type Row =
  | { kind: 'day'; key: string; label: string; count: number | null; top: number; height: number }
  | { kind: 'tiles'; key: string; top: number; height: number; tiles: Tile[] }

export interface JustifyOptions {
  rowHeight: number // the height a row aims for
  gap: number
  dayHeight: number // a day heading's row
  complete: boolean // false: more images may follow, so the last day's count isn't known yet
}

const AR_MIN = 0.4 // an extreme panorama or strip still gets a usable tile
const AR_MAX = 2.5

function rowsOf(items: LayoutItem[], width: number, o: JustifyOptions): { tiles: Tile[]; height: number }[] {
  const out: { tiles: Tile[]; height: number }[] = []
  let run: LayoutItem[] = []
  let sum = 0
  const close = (height: number) => {
    let x = 0
    out.push({ height, tiles: run.map((it) => {
      const w = Math.min(AR_MAX, Math.max(AR_MIN, it.ar)) * height
      const tile = { id: it.id, x, w }
      x += w + o.gap
      return tile
    }) })
    run = []
    sum = 0
  }
  for (const it of items) {
    run.push(it)
    sum += Math.min(AR_MAX, Math.max(AR_MIN, it.ar))
    const room = width - o.gap * (run.length - 1)
    if (sum * o.rowHeight >= room) close(room / sum) // full: stretch it to the width exactly
  }
  if (run.length) close(o.rowHeight) // the day's last row keeps the target height
  return out
}

/** Every row's place for `items` (newest or oldest first, as listed) in a grid `width` wide. */
export function justify(items: LayoutItem[], width: number, o: JustifyOptions): { rows: Row[]; height: number } {
  const rows: Row[] = []
  let top = 0
  let at = 0
  while (at < items.length) {
    const day = items[at].day
    let end = at
    while (end < items.length && items[end].day === day) end++
    const last = end === items.length
    rows.push({ kind: 'day', key: `day:${at}`, label: day, count: last && !o.complete ? null : end - at, top,
      height: o.dayHeight })
    top += o.dayHeight
    for (const r of rowsOf(items.slice(at, end), width, o)) {
      rows.push({ kind: 'tiles', key: `row:${r.tiles[0].id}`, top, height: r.height, tiles: r.tiles })
      top += r.height + o.gap
    }
    at = end
  }
  return { rows, height: top }
}

/** The rows that meet the window [from, to] — what the grid draws. */
export function visibleRows(rows: Row[], from: number, to: number): Row[] {
  let lo = 0
  let hi = rows.length
  while (lo < hi) { // first row ending after `from`
    const mid = (lo + hi) >> 1
    if (rows[mid].top + rows[mid].height < from) lo = mid + 1
    else hi = mid
  }
  const out: Row[] = []
  for (let i = lo; i < rows.length && rows[i].top <= to; i++) out.push(rows[i])
  return out
}
