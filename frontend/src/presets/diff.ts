/* Pure preset helpers (framework-free, unit-tested): strip seed, compare live params to a preset
 * (float-tolerant), and resolve the default preset. */
import type { PanelParams, Preset, PresetParams } from '../types'

// Drop the seed (a preset never stores it) to get the comparable/persistable param subset.
export function stripSeed(p: PanelParams): PresetParams {
  const { seed, ...rest } = p
  void seed
  return rest
}

const EPS = 1e-6 // scale (0.5 step) and cfg_rescale (0.02 step) accumulate float error — compare with tolerance

// True when the live panel params diverge from a preset's — drives the "modified" indicator.
export function presetParamsDiffer(live: PanelParams, preset: PresetParams): boolean {
  const l = stripSeed(live) as Record<string, unknown>
  const p = preset as Record<string, unknown>
  for (const k of Object.keys(p)) {
    const a = l[k]
    const b = p[k]
    if (typeof a === 'number' && typeof b === 'number') {
      if (Math.abs(a - b) > EPS) return true
    } else if (a !== b) {
      return true
    }
  }
  return false
}

export function resolveDefaultId(presets: Preset[]): string | null {
  return presets.find((p) => p.is_default)?.id ?? null
}
