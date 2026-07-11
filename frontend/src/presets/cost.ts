/* Anlas cost estimate for an image generation (framework-free, unit-tested).
 *
 * Community-reverse-engineered formula (novelai-api / novelai-python); v3 and v4/v4.5 all take the
 * same "dimension" branch. Treat the result as an ESTIMATE — NovelAI can re-tune the constants, so
 * reconcile against the real Anlas balance after generating. SMEA doesn't exist on v4, so its factor
 * is 1; the uncond-scale multiplier is left out (only bites on non-default rescale). */
import type { PanelParams, PresetParams } from '../types'

const A = 2951823174884865e-21 // linear-in-area term
const B = 5.753298233447344e-7 // area × steps term

export type CostInputs = Pick<PanelParams | PresetParams, 'width' | 'height' | 'steps' | 'n_samples'>

// Opus (tier 3, active) generates the first sample free at ≤ 1024×1024 and ≤ 28 steps.
export function anlasCost(p: CostInputs, tier = 0, active = false): number {
  const r = Math.max(p.width * p.height, 65536) // floor at 256×256
  const perSample = Math.max(Math.ceil(A * r + B * r * p.steps), 2) // ≥ 2 Anlas/sample
  const opusFree = active && tier >= 3 && p.steps <= 28 && r <= 1024 * 1024
  const n = Math.max(1, p.n_samples)
  return perSample * (n - (opusFree ? 1 : 0))
}

// The two costs worth surfacing regardless of the viewer's own tier: what any paid tier pays
// (`standard`) and what Opus pays (`opus`, with the free first sample). Equal when the free sample
// doesn't apply (over the limits) — the UI then shows a single chip.
export function costPair(p: CostInputs): { standard: number; opus: number } {
  return { standard: anlasCost(p, 0, false), opus: anlasCost(p, 3, true) }
}
