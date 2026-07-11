import { describe, expect, it } from 'vitest'
import { presetParamsDiffer, resolveDefaultId, stripSeed } from './diff'
import type { PanelParams, Preset, PresetParams } from '../types'

const live: PanelParams = {
  model: 'nai-diffusion-4-5-full', width: 832, height: 1216, steps: 28, scale: 5.0,
  sampler: 'k_euler_ancestral', seed: 42, n_samples: 1, noise_schedule: 'karras',
  cfg_rescale: 0.0, quality_toggle: true, uc_preset: 4,
}
const preset: PresetParams = {
  model: 'nai-diffusion-4-5-full', width: 832, height: 1216, steps: 28, scale: 5.0,
  sampler: 'k_euler_ancestral', n_samples: 1, noise_schedule: 'karras',
  cfg_rescale: 0.0, quality_toggle: true, uc_preset: 4,
}

describe('stripSeed', () => {
  it('drops seed and keeps every other param', () => {
    const s = stripSeed(live)
    expect('seed' in s).toBe(false)
    expect(s).toEqual(preset)
  })
})

describe('presetParamsDiffer', () => {
  it('is false when params match (seed ignored)', () => {
    expect(presetParamsDiffer({ ...live, seed: 999 }, preset)).toBe(false)
    expect(presetParamsDiffer({ ...live, seed: null }, preset)).toBe(false)
  })
  it('is true when a param differs', () => {
    expect(presetParamsDiffer({ ...live, steps: 30 }, preset)).toBe(true)
    expect(presetParamsDiffer({ ...live, sampler: 'k_euler' }, preset)).toBe(true)
    expect(presetParamsDiffer({ ...live, quality_toggle: false }, preset)).toBe(true)
  })
  it('tolerates float accumulation on scale / cfg_rescale', () => {
    const drifted = 0.1 + 0.2 - 0.3 // ≈ 5.55e-17, not exactly 0
    expect(presetParamsDiffer({ ...live, cfg_rescale: drifted }, { ...preset, cfg_rescale: 0 })).toBe(false)
    expect(presetParamsDiffer({ ...live, scale: 5.0 + drifted }, { ...preset, scale: 5.0 })).toBe(false)
    expect(presetParamsDiffer({ ...live, scale: 5.5 }, { ...preset, scale: 5.0 })).toBe(true)
  })
})

describe('resolveDefaultId', () => {
  const mk = (id: string, is_default = false): Preset => ({ id, name: id, params: preset, builtin: true, favorite: false, is_default })
  it('returns the default preset id', () => {
    expect(resolveDefaultId([mk('a'), mk('b', true), mk('c')])).toBe('b')
  })
  it('returns null with no default / empty list', () => {
    expect(resolveDefaultId([mk('a'), mk('b')])).toBeNull()
    expect(resolveDefaultId([])).toBeNull()
  })
})
