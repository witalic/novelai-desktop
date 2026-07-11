import { describe, it, expect, vi } from 'vitest'

const { fixture } = vi.hoisted(() => ({
  fixture: {
    models: [{
      id: 'nai-diffusion-4-5-full', label: 'NAI Diffusion 4.5 — Full', family: 'v4', tokenizer: 't5',
      token_limit: 512, negative_token_limit: 512, samplers: ['k_euler_ancestral', 'k_euler'],
      steps: { min: 1, max: 50, step: 1, default: 28 }, scale: { min: 0, max: 10, step: 0.5, default: 5 },
    }],
    samplers: [{ id: 'k_euler_ancestral', label: 'Euler Ancestral' }, { id: 'k_euler', label: 'Euler' }],
    resolutions: [{ group: 'Portrait', tier: 'Normal', width: 832, height: 1216 }],
    uc_presets: [{ value: 4, label: 'Heavy' }],
    noise_schedules: [{ value: 'karras', label: 'karras (recommended)' }],
    dim_limits: { min: 64, max: 2048, step: 64 },
    default_model: 'nai-diffusion-4-5-full',
  },
}))

vi.mock('../api', () => ({ getCatalog: () => Promise.resolve(fixture) }))

import { useCatalog, modelLabel, samplerLabel, sizeLabel, ucLabel, modelSpec } from './useCatalog'

describe('useCatalog', () => {
  it('resolves labels + specs from the fetched catalog', async () => {
    await useCatalog().refresh()
    expect(modelLabel('nai-diffusion-4-5-full')).toBe('NAI Diffusion 4.5 — Full')
    expect(samplerLabel('k_euler')).toBe('Euler')
    expect(sizeLabel(832, 1216)).toBe('Normal — 832×1216')
    expect(ucLabel(4)).toBe('Heavy')
    expect(modelSpec('nai-diffusion-4-5-full')?.tokenizer).toBe('t5')
  })

  it('falls back to the raw value for anything not in the catalog', () => {
    // catalog is loaded from the previous test (singleton) — unknown ids must not throw or blank out.
    expect(modelLabel('nai-diffusion-9')).toBe('nai-diffusion-9')
    expect(samplerLabel('k_unknown')).toBe('k_unknown')
    expect(sizeLabel(100, 200)).toBe('100×200')
    expect(ucLabel(99)).toBe('99')
    expect(modelSpec('nope')).toBeUndefined()
  })
})
