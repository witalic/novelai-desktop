import { describe, expect, it } from 'vitest'
import { justify, visibleRows, type Row } from './justify'

const opts = { rowHeight: 100, gap: 4, dayHeight: 30, complete: true }
const portrait = (id: string, day = 'Today') => ({ id, ar: 0.5, day })

describe('justify', () => {
  it('fills a row to the width exactly, and leaves a day’s last row at the target height', () => {
    // 4 portraits at 100px are 200px + gaps — a 154px-wide grid fits three of them, stretched
    const { rows, height } = justify(['a', 'b', 'c', 'd'].map((id) => portrait(id)), 154, opts)
    expect(rows.map((r) => r.kind)).toEqual(['day', 'tiles', 'tiles'])
    const [, full, last] = rows as [Row, Extract<Row, { kind: 'tiles' }>, Extract<Row, { kind: 'tiles' }>]
    expect(full.tiles.map((t) => t.id)).toEqual(['a', 'b', 'c'])
    const right = full.tiles[2].x + full.tiles[2].w
    expect(right).toBeCloseTo(154)
    expect(full.height).toBeCloseTo((154 - 8) / 1.5)
    expect([last.tiles.map((t) => t.id), last.tiles[0].w, last.height]).toEqual([['d'], 50, 100])
    expect(height).toBeCloseTo(30 + full.height + 4 + 100 + 4)
  })

  it('heads each day, counting it — unless more may follow', () => {
    const items = [portrait('a', 'Today'), portrait('b', 'Yesterday'), portrait('c', 'Yesterday')]
    const days = (complete: boolean) => justify(items, 1000, { ...opts, complete }).rows
      .filter((r): r is Extract<Row, { kind: 'day' }> => r.kind === 'day').map((r) => [r.label, r.count])
    expect(days(true)).toEqual([['Today', 1], ['Yesterday', 2]])
    expect(days(false)).toEqual([['Today', 1], ['Yesterday', null]])
  })

  it('keeps an extreme shape usable', () => {
    const { rows } = justify([{ id: 'strip', ar: 12, day: 'Today' }], 2000, opts)
    expect((rows[1] as Extract<Row, { kind: 'tiles' }>).tiles[0].w).toBe(250) // capped at 2.5 × 100
  })
})

describe('visibleRows', () => {
  it('draws only the rows that meet the window', () => {
    const { rows } = justify(Array.from({ length: 30 }, (_, i) => portrait(`i${i}`)), 154, opts)
    const shown = visibleRows(rows, 400, 600)
    expect(shown.length).toBeGreaterThan(0)
    expect(shown.every((r) => r.top <= 600 && r.top + r.height >= 400)).toBe(true)
    expect(visibleRows(rows, 0, 0)[0]).toBe(rows[0])
  })
})
