/* Anlas cost estimate for an image generation (framework-free, unit-tested).
 *
 * NovelAI's own rule as its web client computes it (novelai.net/image, read 2026-11-15); the server keeps a copy in
 * backend/app/novelai/cost.py — keep the two in step. NovelAI can re-tune it, so the balance stays the final word.
 * A V5 image costs 1.5× the area-and-steps base. NovelAI also refuses an image over 140 Anlas — the server's copy
 * checks that; the UI doesn't flag it yet. SMEA doesn't exist on v4+, so its factor is 1. */
import type { PanelParams, PresetParams } from '../types'

const A = 2951823174884865e-21 // linear-in-area term
const B = 5.753298233447344e-7 // area × steps term
const V5_FACTOR = 1.5

export type CostInputs = Pick<PanelParams | PresetParams, 'model' | 'width' | 'height' | 'steps' | 'n_samples'>

const isV5 = (model: string) => model.startsWith('nai-diffusion-5')

function perImageCost(p: CostInputs): number {
  const area = p.width * p.height
  const base = Math.ceil(A * area + B * area * p.steps)
  return Math.max(isV5(p.model) ? Math.ceil(base * V5_FACTOR) : base, 2) // ≥ 2 Anlas per image
}

// Opus (tier 3, active) generates one sample free at ≤ 1024×1024 and ≤ 28 steps — on V5 only while its usage limit
// isn't used up (v5LimitReached).
export function anlasCost(p: CostInputs, tier = 0, active = false, v5LimitReached = false): number {
  const opusFree = active && tier >= 3 && !(v5LimitReached && isV5(p.model))
    && p.width * p.height <= 1024 * 1024 && p.steps <= 28
  const n = Math.max(1, p.n_samples)
  return perImageCost(p) * (n - (opusFree ? 1 : 0))
}

// The two costs worth surfacing regardless of the viewer's own tier: what any paid tier pays
// (`standard`) and what Opus pays (`opus`, with the free first sample). Equal when the free sample
// doesn't apply (over the limits) — the UI then shows a single chip.
export function costPair(p: CostInputs): { standard: number; opus: number } {
  return { standard: anlasCost(p, 0, false), opus: anlasCost(p, 3, true) }
}
