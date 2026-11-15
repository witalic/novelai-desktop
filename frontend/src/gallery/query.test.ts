import { describe, expect, it } from 'vitest'
import { dayHeading, galleryParams, NO_FILTERS, periodBounds, periodLabel } from './query'

const now = new Date(2026, 9, 27, 15, 30) // 27 Oct 2026, mid-afternoon, local time

describe('periodBounds', () => {
  it('counts in the viewer’s own days', () => {
    expect(periodBounds({ kind: 'today' }, now)).toEqual({ since: new Date(2026, 9, 27) })
    expect(periodBounds({ kind: 'week' }, now)).toEqual({ since: new Date(2026, 9, 21) }) // today + six before
    expect(periodBounds({ kind: 'month' }, now)).toEqual({ since: new Date(2026, 8, 28) })
    expect(periodBounds({ kind: 'any' }, now)).toEqual({})
  })

  it('a range includes its last day, across a month end', () => {
    expect(periodBounds({ kind: 'range', from: '2026-10-20', to: '2026-10-31' }, now))
      .toEqual({ since: new Date(2026, 9, 20), until: new Date(2026, 10, 1) })
  })
})

describe('galleryParams', () => {
  it('asks for images in albums only, newest first, by default', () => {
    expect(galleryParams(NO_FILTERS, now).toString()).toBe('unsorted=exclude')
  })

  it('carries every filter, a period as moments with their offset', () => {
    const p = galleryParams({
      ...NO_FILTERS, q: ' moon ', tags: ['night', 'luminous'], works: ['w-1'], models: ['m-1', 'm-2'], favorite: true,
      period: { kind: 'today' }, unsorted: true,
    }, now)
    expect(p.get('q')).toBe('moon')
    expect(p.getAll('tags')).toEqual(['night', 'luminous'])
    expect(p.getAll('work')).toEqual(['w-1'])
    expect(p.getAll('model')).toEqual(['m-1', 'm-2'])
    expect(p.get('favorite')).toBe('true')
    expect(p.get('since')).toBe(new Date(2026, 9, 27).toISOString())
    expect(p.get('unsorted')).toBe('include')
  })
})

describe('dayHeading', () => {
  it('names the days near now, and dates the rest', () => {
    expect(dayHeading(new Date(2026, 9, 27, 0, 5).toISOString(), now)).toBe('Today')
    expect(dayHeading(new Date(2026, 9, 26, 23, 59).toISOString(), now)).toBe('Yesterday')
    expect(dayHeading(new Date(2026, 9, 23, 12).toISOString(), now)).toBe('Friday') // in English, whatever the system's
    expect(dayHeading(new Date(2026, 9, 2, 12).toISOString(), now)).toBe('Fri 2 Oct')
    expect(dayHeading(new Date(2025, 9, 2, 12).toISOString(), now)).toBe('Thu, 2 Oct 2025')
    expect(dayHeading('not a date', now)).toBe('Undated')
    expect(periodLabel({ kind: 'range', from: '2026-10-20', to: '2026-10-27' })).toBe('20 Oct – 27 Oct')
  })
})
