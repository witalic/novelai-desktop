import { describe, expect, it } from 'vitest'
import { dedupePrompt } from './dedup'

describe('dedupePrompt', () => {
  it('leaves a prompt with no duplicates unchanged', () => {
    expect(dedupePrompt('1girl, long hair, blue eyes')).toBe('1girl, long hair, blue eyes')
  })

  it('merges plain duplicates and sums their weights (capped)', () => {
    // long hair × 3 → 3.0, capped at 1.5
    expect(dedupePrompt('long hair, blue eyes, long hair, long hair')).toBe('1.5::long hair::, blue eyes')
  })

  it('is case-insensitive when merging but keeps the first display form', () => {
    expect(dedupePrompt('Long Hair, long hair')).toBe('1.5::Long Hair::')
  })

  it('sums a weighted tag with a plain one', () => {
    // 1.3 + 1.0 = 2.3 → capped 1.5
    expect(dedupePrompt('1.3::long hair::, long hair')).toBe('1.5::long hair::')
  })

  it('keeps a single weighted tag exactly (never capped)', () => {
    expect(dedupePrompt('2::detailed eyes::, 1girl')).toBe('2::detailed eyes::, 1girl')
  })

  it('treats a weighted group as atomic even with an inner comma', () => {
    expect(dedupePrompt('1.3::a, b::, c')).toBe('1.3::a, b::, c')
  })

  it('handles empty / whitespace', () => {
    expect(dedupePrompt('')).toBe('')
    expect(dedupePrompt('  ,  , ')).toBe('')
  })
})
