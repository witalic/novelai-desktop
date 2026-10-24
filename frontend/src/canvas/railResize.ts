// Drag a widget rail's edge to resize it (design/ui-polish.html §4). Works inside the zoomed canvas: the
// pointer's screen movement is divided by the rail's on-screen scale. The width is the caller's to store —
// on the zone/station node data, so it saves with the work's layout like the station's zone split.
export const RAIL_MIN = 140
export const RAIL_MAX = 320

// `edge`: which side of the rail the handle sits on — dragging away from the rail widens it.
export function startRailResize(e: PointerEvent, rail: HTMLElement, edge: 'left' | 'right', apply: (width: number) => void): void {
  e.preventDefault()
  e.stopPropagation() // never a node drag
  const scale = rail.getBoundingClientRect().width / (rail.offsetWidth || 1) || 1
  const startX = e.clientX
  const startW = rail.offsetWidth
  const move = (ev: PointerEvent) => {
    const dx = (ev.clientX - startX) / scale
    apply(Math.round(Math.min(RAIL_MAX, Math.max(RAIL_MIN, edge === 'right' ? startW + dx : startW - dx))))
  }
  const up = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    window.removeEventListener('pointercancel', up)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
  window.addEventListener('pointercancel', up)
}
