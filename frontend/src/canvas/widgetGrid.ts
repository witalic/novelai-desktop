/* The widget layout grid (design/widget-grid.html). Widgets — the Prompt blocks, Combinations and Gallery
 * zones and the Generation station — sit on their own 40px module grid, apart from the canvas dots that loose
 * images and blocks keep. A dragged or resized widget lands on whole modules, and an edge that comes near a
 * neighbour's is pulled to it: the edges line up, or a one-module gap is kept. Pure — the canvas feeds rects in. */

export const MODULE = 40
export const GAP = MODULE

export interface Rect { x: number; y: number; w: number; h: number }
// A guide line to draw while snapping: at x (a vertical line) or at y (a horizontal one), spanning from→to on
// the other axis. `gap` marks the middle of a kept 40px gap rather than a shared edge.
export interface Guide { axis: 'x' | 'y'; at: number; from: number; to: number; gap?: boolean }
export interface Snap { rect: Rect; guides: Guide[] }

export type WidgetKey = 'library' | 'combos' | 'station' | 'gallery'

// A new work's layout: a left column (Prompt blocks over Combinations) as tall as the station and the gallery.
export const DEFAULT_LAYOUT: Record<WidgetKey, Rect> = {
  library: { x: 40, y: 40, w: 520, h: 560 },
  combos: { x: 40, y: 640, w: 520, h: 360 },
  station: { x: 600, y: 40, w: 960, h: 960 },
  gallery: { x: 1600, y: 40, w: 480, h: 960 },
}
export const MIN_SIZE: Record<WidgetKey, { w: number; h: number }> = {
  library: { w: 320, h: 280 },
  combos: { w: 320, h: 240 },
  station: { w: 560, h: 320 },
  gallery: { w: 360, h: 280 },
}

export const toModule = (v: number) => Math.round(v / MODULE) * MODULE

export function boundsOf(rects: Rect[]): Rect {
  const x = Math.min(...rects.map((r) => r.x))
  const y = Math.min(...rects.map((r) => r.y))
  return { x, y, w: Math.max(...rects.map((r) => r.x + r.w)) - x, h: Math.max(...rects.map((r) => r.y + r.h)) - y }
}

// The viewport that fits `r` into a w×h container with `pad` screen px around it (zoom capped to [min, max]).
export function fitRect(r: Rect, box: { w: number; h: number }, pad = 24, min = 0.2, max = 1): { x: number; y: number; zoom: number } {
  const zoom = Math.min(max, Math.max(min, Math.min((box.w - pad * 2) / r.w, (box.h - pad * 2) / r.h)))
  return { x: (box.w - r.w * zoom) / 2 - r.x * zoom, y: (box.h - r.h * zoom) / 2 - r.y * zoom, zoom }
}

interface EdgeHit { delta: number; guide: Guide }

// The nearest pull for one edge (`side` 'start' = left/top, 'end' = right/bottom) at `pos` on one axis: line up
// with a neighbour's matching edge, or keep a GAP from its facing edge. Null if none is within `tol`.
function edgePull(axis: 'x' | 'y', side: 'start' | 'end', pos: number, rect: Rect, others: Rect[], tol: number): EdgeHit | null {
  let best: EdgeHit | null = null
  const span = (o: Rect) => (axis === 'x'
    ? { from: Math.min(rect.y, o.y), to: Math.max(rect.y + rect.h, o.y + o.h) }
    : { from: Math.min(rect.x, o.x), to: Math.max(rect.x + rect.w, o.x + o.w) })
  for (const o of others) {
    const oStart = axis === 'x' ? o.x : o.y
    const oEnd = oStart + (axis === 'x' ? o.w : o.h)
    const targets = side === 'start'
      ? [{ at: oStart, line: oStart, gap: false }, { at: oEnd + GAP, line: oEnd + GAP / 2, gap: true }]
      : [{ at: oEnd, line: oEnd, gap: false }, { at: oStart - GAP, line: oStart - GAP / 2, gap: true }]
    for (const t of targets) {
      const delta = t.at - pos
      if (Math.abs(delta) <= tol && (!best || Math.abs(delta) < Math.abs(best.delta))) {
        best = { delta, guide: { axis, at: t.line, ...span(o), ...(t.gap ? { gap: true } : {}) } }
      }
    }
  }
  return best
}

// A moved widget: per axis, the nearest neighbour pull on either edge, else its start edge onto the grid.
export function snapMove(rect: Rect, others: Rect[], tol: number): Snap {
  const out = { ...rect }
  const guides: Guide[] = []
  for (const axis of ['x', 'y'] as const) {
    const start = axis === 'x' ? rect.x : rect.y
    const size = axis === 'x' ? rect.w : rect.h
    const a = edgePull(axis, 'start', start, rect, others, tol)
    const b = edgePull(axis, 'end', start + size, rect, others, tol)
    const hit = a && b ? (Math.abs(a.delta) <= Math.abs(b.delta) ? a : b) : a ?? b
    const delta = hit ? hit.delta : toModule(start) - start
    if (axis === 'x') out.x += delta
    else out.y += delta
    if (hit) guides.push(hit.guide)
  }
  return { rect: out, guides }
}

// A resized widget: each edge that moved (vs `before`) is pulled to a neighbour or onto the grid; the size
// never drops below `min`.
export function snapResize(rect: Rect, before: Rect, others: Rect[], tol: number, min: { w: number; h: number }): Snap {
  const guides: Guide[] = []
  let left = rect.x
  let top = rect.y
  let right = rect.x + rect.w
  let bottom = rect.y + rect.h
  const pull = (axis: 'x' | 'y', side: 'start' | 'end', pos: number) => {
    const hit = edgePull(axis, side, pos, rect, others, tol)
    if (hit) guides.push(hit.guide)
    return hit ? pos + hit.delta : toModule(pos)
  }
  if (rect.x !== before.x) left = Math.min(pull('x', 'start', left), right - min.w)
  if (rect.x + rect.w !== before.x + before.w) right = Math.max(pull('x', 'end', right), left + min.w)
  if (rect.y !== before.y) top = Math.min(pull('y', 'start', top), bottom - min.h)
  if (rect.y + rect.h !== before.y + before.h) bottom = Math.max(pull('y', 'end', bottom), top + min.h)
  return { rect: { x: left, y: top, w: right - left, h: bottom - top }, guides }
}
