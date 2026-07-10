export interface GenerateParams {
  prompt: string
  negative_prompt: string
  model: string
  width: number
  height: number
  steps: number
  scale: number
  sampler: string
  seed: number | null
  n_samples: number
  noise_schedule: string
  cfg_rescale: number
  quality_toggle: boolean
  uc_preset: number
}

export type PanelParams = Omit<GenerateParams, 'prompt' | 'negative_prompt'>

export interface GenerateResponse {
  mock: boolean
  count: number
  images: string[] // base64-encoded PNGs
}

export type StreamEvent =
  | { type: 'intermediate'; samp: number; step: number; mime: string; image: string }
  | { type: 'final'; mime: string; image: string }
  | { type: 'error'; message: string; status: number }

export interface SnapshotData {
  components: Record<string, unknown>[]
  positive: string
  negative: string
  params: Record<string, unknown> // the generation recipe lives in the snapshot, not the image
  hash: string
}

export interface GenResult {
  id: string // img-<uuid>, stable so the stack persists with the work
  url: string // data URL (fresh) or /api/vault/works/…/images/… (restored)
  file?: string // set when restored from a saved work
  params: GenerateParams
  mock: boolean
  snapshot?: SnapshotData
}

// Vault documents (loosely typed — the frontend owns the canvas shape, the backend stores it opaquely).
export type WorkDoc = Record<string, unknown> & { id: string; title: string }

export interface WorkListItem {
  id: string
  title: string
  updated_at: string
  image_count: number
  preview_url: string | null
}

export interface WorksPage {
  items: WorkListItem[]
  total: number
  page: number
  per_page: number
}

// ---- library ----
export interface LibraryBlock {
  id: string
  category: string // category slug
  name: string
  text: string
  polarity: 'positive' | 'negative'
  tags: string[]
  version?: number
  created_at?: string
  updated_at?: string
}

export interface BlocksPage {
  items: LibraryBlock[]
  total: number
  page: number
  per_page: number
}

export interface CategoryCount {
  slug: string
  name: string
  color: string
  count: number
  builtin: boolean
}

export interface TagCount {
  name: string
  count: number
}
