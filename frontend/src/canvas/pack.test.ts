import { describe, expect, it } from 'vitest'
import { appendX, insertionIndex, packColumn, packedHeight, PACK_GAP, PACK_TOP, PACK_X } from './pack'

describe('packColumn', () => {
  it('stacks rows top-to-bottom in palette order with a constant gap', () => {
    const pos = packColumn([{ id: 'a', h: 34 }, { id: 'b', h: 132 }, { id: 'c', h: 34 }])
    expect(pos).toEqual([
      { id: 'a', x: PACK_X, y: PACK_TOP },
      { id: 'b', x: PACK_X, y: PACK_TOP + 34 + PACK_GAP },
      { id: 'c', x: PACK_X, y: PACK_TOP + 34 + PACK_GAP + 132 + PACK_GAP },
    ])
  })

  it('handles an empty palette', () => {
    expect(packColumn([])).toEqual([])
    expect(packedHeight([])).toBeGreaterThan(PACK_TOP) // header + footer still need room
  })
})

describe('packedHeight', () => {
  it('covers the last row plus footer room', () => {
    const items = [{ id: 'a', h: 34 }, { id: 'b', h: 34 }]
    const last = packColumn(items).at(-1)!
    expect(packedHeight(items)).toBeGreaterThan(last.y + 34)
  })
})

describe('insertionIndex', () => {
  const rows = packColumn([{ id: 'a', h: 34 }, { id: 'b', h: 34 }, { id: 'c', h: 34 }])
    .map((p) => ({ y: p.y, h: 34 }))

  it('drops above everything → slot 0', () => {
    expect(insertionIndex(rows, 0)).toBe(0)
  })
  it('drops between rows → the slot at the crossed midline', () => {
    expect(insertionIndex(rows, rows[0].y + 34)).toBe(1) // just past a's midline, before b's
    expect(insertionIndex(rows, rows[2].y)).toBe(2)      // above c's midline
  })
  it('drops below everything → append', () => {
    expect(insertionIndex(rows, 10_000)).toBe(3)
  })
  it('empty palette → slot 0', () => {
    expect(insertionIndex([], 123)).toBe(0)
  })
})

describe('appendX', () => {
  it('appends after the rightmost block in the lane', () => {
    expect(appendX([{ x: 250, w: 176 }, { x: 440, w: 176 }], 240)).toBe(440 + 176 + 10)
  })
  it('starts just inside an empty lane', () => {
    expect(appendX([], 240)).toBe(252)
  })
})
