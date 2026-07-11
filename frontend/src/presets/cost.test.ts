import { describe, expect, it } from 'vitest'
import { anlasCost, costPair } from './cost'

const base = { width: 1024, height: 1024, steps: 28, n_samples: 1 }

describe('anlasCost', () => {
  it('a full-size 28-step image is ~20 Anlas for a non-Opus tier', () => {
    expect(anlasCost(base, 1, true)).toBe(20) // known NovelAI sanity value
  })
  it('Opus gets the first sample free at ≤1024² / ≤28 steps', () => {
    expect(anlasCost(base, 3, true)).toBe(0)
    expect(anlasCost({ ...base, n_samples: 3 }, 3, true)).toBe(40) // 3 samples, one free → 2×20
  })
  it('no Opus discount above 1024² or 28 steps', () => {
    expect(anlasCost({ ...base, steps: 50 }, 3, true)).toBeGreaterThan(0)
    expect(anlasCost({ width: 1088, height: 1920, steps: 28, n_samples: 1 }, 3, true)).toBeGreaterThan(0)
  })
  it('inactive Opus tier pays full', () => {
    expect(anlasCost(base, 3, false)).toBe(20)
  })
  it('cost scales with samples (no discount tier)', () => {
    expect(anlasCost({ ...base, n_samples: 2 }, 0, false)).toBe(40)
  })
  it('floors resolution at 256×256 and cost at 2/sample', () => {
    expect(anlasCost({ width: 64, height: 64, steps: 1, n_samples: 1 }, 0, false)).toBe(2)
  })
})

describe('costPair', () => {
  it('differs when the Opus free sample applies (within the limits)', () => {
    expect(costPair(base)).toEqual({ standard: 20, opus: 0 })
  })
  it('is equal when over the limits (no Opus discount)', () => {
    const big = { width: 1088, height: 1920, steps: 32, n_samples: 1 }
    const { standard, opus } = costPair(big)
    expect(standard).toBe(opus)
  })
})
