import { describe, expect, it } from 'vitest'
import { anlasCost, costPair } from './cost'

// The V5 and V4.5 figures were read off the account's balance around real generations (2026-11-15); the server's
// copy (backend/tests/test_cost.py) checks the same cases.
const v5 = { model: 'nai-diffusion-5-full', width: 1024, height: 1024, steps: 28, n_samples: 1 }
const v45 = { ...v5, model: 'nai-diffusion-4-5-full' }

describe('anlasCost', () => {
  it('V5 costs half again the base that V4.5 pays', () => {
    expect(anlasCost({ ...v45, width: 832, height: 1216 })).toBe(20)
    expect(anlasCost({ ...v5, width: 832, height: 1216 })).toBe(30)
    expect(anlasCost({ ...v5, width: 1088, height: 1920 })).toBe(60)
    expect(anlasCost({ ...v5, model: 'nai-diffusion-5-curated', width: 832, height: 1216 })).toBe(30)
  })
  it('V4.5 pays the plain base, even for a big image', () => {
    expect(anlasCost({ ...v45, width: 2048, height: 1536, steps: 50 })).toBe(100)
  })
  it('Opus gets the first sample free at ≤1024² / ≤28 steps', () => {
    expect(anlasCost(v5, 3, true)).toBe(0)
    expect(anlasCost({ ...v5, n_samples: 3 }, 3, true)).toBe(60) // 3 samples, one free → 2×30
  })
  it('a used-up V5 usage limit ends the free V5 sample only', () => {
    expect(anlasCost(v5, 3, true, true)).toBe(30)
    expect(anlasCost(v45, 3, true, true)).toBe(0)
  })
  it('no Opus discount above 1024² or 28 steps', () => {
    expect(anlasCost({ ...v5, steps: 50 }, 3, true)).toBeGreaterThan(0)
    expect(anlasCost({ ...v5, width: 1088, height: 1920 }, 3, true)).toBe(60)
  })
  it('inactive Opus tier pays full', () => {
    expect(anlasCost(v5, 3, false)).toBe(30)
  })
  it('cost scales with samples and has a floor of 2 per image', () => {
    expect(anlasCost({ ...v45, n_samples: 2 })).toBe(40)
    expect(anlasCost({ ...v5, width: 64, height: 64, steps: 1 })).toBe(2)
  })
})

describe('costPair', () => {
  it('differs when the Opus free sample applies (within the limits)', () => {
    expect(costPair(v5)).toEqual({ standard: 30, opus: 0 })
  })
  it('is equal when over the limits (no Opus discount)', () => {
    const big = { ...v5, width: 1088, height: 1920, steps: 32 }
    const { standard, opus } = costPair(big)
    expect(standard).toBe(opus)
  })
})
