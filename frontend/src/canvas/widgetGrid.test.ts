import { describe, expect, it } from 'vitest'
import { DEFAULT_LAYOUT, boundsOf, fitRect, snapMove, snapResize } from './widgetGrid'

const library = DEFAULT_LAYOUT.library // 40,40 520×560
const combos = DEFAULT_LAYOUT.combos // 40,640 520×360
const gallery = DEFAULT_LAYOUT.gallery // 1600,40 480×960

describe('default layout', () => {
  it('stacks the left column to the station’s height and keeps 40px gaps on whole modules', () => {
    const { station } = DEFAULT_LAYOUT
    expect(combos.y + combos.h).toBe(station.y + station.h) // bottoms line up
    expect(station.x - (library.x + library.w)).toBe(40)
    expect(gallery.x - (station.x + station.w)).toBe(40)
    for (const r of Object.values(DEFAULT_LAYOUT)) for (const v of [r.x, r.y, r.w, r.h]) expect(v % 40).toBe(0)
  })

  it('fits a rect into a window, centred, never above 100%', () => {
    const all = boundsOf(Object.values(DEFAULT_LAYOUT))
    expect(all).toEqual({ x: 40, y: 40, w: 2040, h: 960 })
    const v = fitRect(all, { w: 1040, h: 600 }, 10) // width-bound: (1040 − 20) / 2040 = 0.5
    expect(v.zoom).toBeCloseTo(0.5)
    expect(v.x + all.x * v.zoom).toBeCloseTo((1040 - all.w * v.zoom) / 2) // centred horizontally
    expect(fitRect({ x: 0, y: 0, w: 100, h: 100 }, { w: 2000, h: 2000 }).zoom).toBe(1)
  })
})

describe('moving a widget', () => {
  it('keeps a 40px gap from a neighbour and lines its top up', () => {
    // the station dragged near Prompt blocks: left edge 8px off the gap, top 6px off the shared top
    const { rect, guides } = snapMove({ x: 608, y: 46, w: 960, h: 960 }, [library, combos, gallery], 16)
    expect(rect).toMatchObject({ x: 600, y: 40 })
    expect(guides).toContainEqual(expect.objectContaining({ axis: 'x', at: 580, gap: true }))
    expect(guides).toContainEqual(expect.objectContaining({ axis: 'y', at: 40 }))
  })

  it('lands on whole modules when no neighbour is near', () => {
    const { rect, guides } = snapMove({ x: 3013, y: 2587, w: 400, h: 300 }, [library], 16)
    expect(rect).toMatchObject({ x: 3000, y: 2600 })
    expect(guides).toEqual([])
  })
})

describe('resizing a widget', () => {
  it('pulls the moving edge to a neighbour’s edge and leaves the others', () => {
    const before = { x: 600, y: 40, w: 960, h: 600 }
    const { rect, guides } = snapResize({ x: 600, y: 40, w: 960, h: 953 }, before, [combos, gallery], 16, { w: 560, h: 320 })
    expect(rect).toEqual({ x: 600, y: 40, w: 960, h: 960 }) // bottom lines up with the column's bottom (1000)
    expect(guides[0]).toMatchObject({ axis: 'y', at: 1000 })
  })

  it('rounds to whole modules and never shrinks below the minimum', () => {
    const before = { x: 3000, y: 2000, w: 800, h: 800 }
    expect(snapResize({ x: 3000, y: 2000, w: 813, h: 800 }, before, [], 16, { w: 560, h: 320 }).rect.w).toBe(800)
    expect(snapResize({ x: 3000, y: 2000, w: 500, h: 800 }, before, [], 16, { w: 560, h: 320 }).rect.w).toBe(560)
    // dragging the left edge moves x and keeps the right edge
    const left = snapResize({ x: 3087, y: 2000, w: 713, h: 800 }, before, [], 16, { w: 560, h: 320 }).rect
    expect([left.x, left.x + left.w]).toEqual([3080, 3800])
  })
})
