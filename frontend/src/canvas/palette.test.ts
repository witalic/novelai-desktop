import { describe, expect, it } from 'vitest'
import { isStale, reorderIds } from './palette'

describe('reorderIds', () => {
  const ids = ['a', 'b', 'c', 'd']

  it('moves before a target, keeping the rest in order', () => {
    expect(reorderIds(ids, 'd', 'b')).toEqual(['a', 'd', 'b', 'c'])
    expect(reorderIds(ids, 'a', 'c')).toEqual(['b', 'a', 'c', 'd'])
  })
  it('moves to the end when beforeId is null', () => {
    expect(reorderIds(ids, 'b', null)).toEqual(['a', 'c', 'd', 'b'])
  })
  it('is a no-op when the moved id is unknown', () => {
    expect(reorderIds(ids, 'x', 'a')).toEqual(ids)
  })
  it('handles moving before an unknown target → appends', () => {
    expect(reorderIds(ids, 'a', 'zzz')).toEqual(['b', 'c', 'd', 'a'])
  })
})

describe('isStale', () => {
  it('flags a pin whose Library block moved to a higher version', () => {
    expect(isStale(1, 2)).toBe(true)
    expect(isStale(2, 2)).toBe(false)
    expect(isStale(3, 2)).toBe(false) // never downgrade
  })
  it('defaults missing versions to 1', () => {
    expect(isStale(undefined, 2)).toBe(true)
    expect(isStale(1, undefined)).toBe(false)
  })
})
