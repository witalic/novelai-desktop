/* The NovelAI model catalog (backend/app/novelai/catalog.py) — the single source of model / sampler /
 * resolution / step knowledge. Fetched once and cached in singleton module state (like useAccount), so
 * the param fields, preset editor, and preset cards all read the same facts. Label helpers resolve from
 * the fetched catalog and fall back to the raw slug until it loads (a stored value is never lost). */
import { ref } from 'vue'
import { getCatalog } from '../api'
import type { Catalog, CatalogModel } from '../types'

const catalog = ref<Catalog | null>(null)
let inflight: Promise<unknown> | null = null

function refresh() {
  if (!inflight && !catalog.value) {
    inflight = getCatalog()
      .then((c) => { catalog.value = c })
      .catch(() => { /* backend not ready / offline — consumers fall back to raw slugs + null ranges */ })
      .finally(() => { inflight = null })
  }
  return inflight
}

// ---- label + lookup helpers (reactive: reading catalog.value tracks in a render) ----
export const modelSpec = (id: string): CatalogModel | undefined => catalog.value?.models.find((m) => m.id === id)
export const modelLabel = (id: string): string => modelSpec(id)?.label ?? id
export const samplerLabel = (id: string): string => catalog.value?.samplers.find((s) => s.id === id)?.label ?? id
export const ucLabel = (v: number): string => catalog.value?.uc_presets.find((u) => u.value === v)?.label ?? String(v)

// Matched size tier ("Normal — 832×1216") or a bare "832×1216" for a custom size.
export function sizeLabel(w: number, h: number): string {
  const r = catalog.value?.resolutions.find((x) => x.width === w && x.height === h)
  return r ? `${r.tier} — ${w}×${h}` : `${w}×${h}`
}

export function useCatalog() {
  refresh() // any consumer mounting triggers the one-shot fetch; coalesced
  return { catalog, refresh }
}
