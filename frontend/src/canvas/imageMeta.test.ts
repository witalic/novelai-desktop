import { describe, expect, it } from 'vitest'
import { imageMeta } from './imageMeta'

describe('imageMeta', () => {
  const now = Date.parse('2026-10-24T12:00:00Z')

  it('joins size, seed and a relative time', () => {
    expect(imageMeta({ width: 832, height: 1216, seed: 42 }, '2026-10-24T11:58:00Z', now)).toBe('832×1216 · seed 42 · 2 min ago')
    expect(imageMeta({ width: 832, height: 1216, seed: 0 }, '2026-10-24T11:59:40Z', now)).toBe('832×1216 · seed 0 · just now')
    expect(imageMeta(undefined, '2026-10-24T09:00:00Z', now)).toBe('3 h ago')
  })

  it('leaves unknown parts out', () => {
    expect(imageMeta({ seed: 7 }, undefined, now)).toBe('seed 7')
    expect(imageMeta(undefined, 'not a date', now)).toBe('')
  })
})
