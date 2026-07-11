/* Shared generation-param option maps + label lookups. Extracted from ParamsPanel so the panel,
 * the preset editor, and preset cards all render identical labels from the same stored slugs. */

export const MODELS = [
  { value: 'nai-diffusion-4-5-full', label: 'NAI Diffusion 4.5 — Full' },
  { value: 'nai-diffusion-4-5-curated', label: 'NAI Diffusion 4.5 — Curated' },
  { value: 'nai-diffusion-3', label: 'NAI Diffusion 3' },
]

// Sizes grouped by aspect ratio: portrait, then landscape, then square.
export const SIZES = [
  { group: 'Portrait', items: [{ tier: 'Small', w: 512, h: 768 }, { tier: 'Normal', w: 832, h: 1216 }, { tier: 'Large', w: 1024, h: 1536 }, { tier: 'Wallpaper', w: 1088, h: 1920 }] },
  { group: 'Landscape', items: [{ tier: 'Small', w: 768, h: 512 }, { tier: 'Normal', w: 1216, h: 832 }, { tier: 'Large', w: 1536, h: 1024 }, { tier: 'Wallpaper', w: 1920, h: 1088 }] },
  { group: 'Square', items: [{ tier: 'Small', w: 640, h: 640 }, { tier: 'Normal', w: 1024, h: 1024 }, { tier: 'Large', w: 1472, h: 1472 }] },
]

export const SAMPLERS = [
  { value: 'k_euler_ancestral', label: 'Euler Ancestral' },
  { value: 'k_euler', label: 'Euler' },
  { value: 'k_dpmpp_2s_ancestral', label: 'DPM++ 2S Ancestral' },
  { value: 'k_dpmpp_2m_sde', label: 'DPM++ 2M SDE' },
  { value: 'k_dpmpp_2m', label: 'DPM++ 2M' },
  { value: 'k_dpmpp_sde', label: 'DPM++ SDE' },
]

export const UC_PRESETS = [
  { value: 4, label: 'Heavy' },
  { value: 5, label: 'Light' },
  { value: 7, label: 'Furry Focus' },
  { value: 6, label: 'Human Focus' },
  { value: 3, label: 'None' },
]

export const NOISE = [
  { value: 'karras', label: 'karras (recommended)' },
  { value: 'exponential', label: 'exponential' },
  { value: 'polyexponential', label: 'polyexponential' },
]

export const modelLabel = (slug: string) => MODELS.find((m) => m.value === slug)?.label ?? slug
export const samplerLabel = (slug: string) => SAMPLERS.find((s) => s.value === slug)?.label ?? slug
export const ucLabel = (v: number) => UC_PRESETS.find((u) => u.value === v)?.label ?? String(v)

// Matched size tier ("Normal — 832×1216") or a bare "832×1216" for a custom size.
export function sizeLabel(w: number, h: number): string {
  for (const g of SIZES) for (const it of g.items) if (it.w === w && it.h === h) return `${it.tier} — ${w}×${h}`
  return `${w}×${h}`
}
