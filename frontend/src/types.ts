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
  | { type: 'final'; mime: string; image: string; seed: number; mock?: boolean }
  | { type: 'error'; message: string; status: number }

export interface SnapshotData {
  components: PersistedComponent[]
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

// ---- vault recipe contract (the persisted WorkDoc) ----
// Manual parity with backend/app/vault/models.py — when one side changes shape, change the other
// and bump schema_version + add a migration (ROADMAP Phase 1). Domain data (snapshots, images,
// stack, params) is typed on BOTH sides; canvas layout is frontend-owned and the backend stores it
// opaquely (deliberate deviation from the roadmap letter: the backend never interprets the canvas,
// and validating layout there would turn every frontend-only layout change into a backend break).

export interface PersistedComponent {
  source: 'library' | 'custom'
  block_id?: string | null // Library provenance ref — `text` below stays the frozen copy
  version?: number | null
  name: string
  text: string
  polarity: 'positive' | 'negative'
  category?: string | null
  tags: string[]
}

// A snapshot is a frozen recipe: resolved text + params + seed at generation time, plus the
// component refs it was assembled from. Editing a Library block never mutates past snapshots.
export interface PersistedSnapshot {
  id: string
  hash: string
  components: PersistedComponent[]
  assembled_positive: string
  assembled_negative: string
  params: Record<string, unknown>
  created_at?: string
}

export interface PersistedImage {
  id: string
  snapshot_id: string | null
  file: string
  image_b64?: string | null // inline bytes on save; the backend writes the file and never persists this
  created_at: string
  group: string | null
  favorite: boolean
  tags: string[] // manual tags (inherited ones come from the snapshot)
  description: string
  source?: string
}

// The generation output pile ("stack") — persists so reopening a work restores the drafts.
export interface PersistedStackItem {
  id: string
  snapshot_id: string | null
  file: string
  image_b64?: string | null
  created_at: string
}

// ---- canvas nodes (layout — frontend-owned, backend-opaque) ----

export interface Viewport {
  x: number
  y: number
  zoom: number
}

export interface NodeStyle {
  width?: string
  height?: string
}

interface CanvasNodeBase {
  id: string
  position: { x: number; y: number }
  parentNode?: string // an anchor id (station/library/gallery) or absent = loose on the canvas
  style?: NodeStyle
  zIndex?: number
}

// The generation station: [ Output | Positive / Negative lanes ]; internal areas are ratio-driven.
export interface StationNode extends CanvasNodeBase {
  type: 'station'
  data: { outputRatio?: number; posRatio?: number }
}

export interface ZoneNode extends CanvasNodeBase {
  type: 'zone'
  data: { role: 'library' | 'gallery' }
}

export interface BlockNodeData {
  // domain — the block's content (a frozen copy even when block_id links it to the Library)
  category: string
  name: string
  text: string
  polarity: 'positive' | 'negative'
  block_id?: string
  version?: number
  tags?: string[]
  // layout — relative placement within the station's polarity lane
  xFrac?: number
  laneFrac?: number
  // transient UI state — never persisted (serialize.ts whitelists it out)
  expanded?: boolean
  editing?: boolean
  _cw?: string // collapsed size remembered across expand/collapse
  _ch?: string
}

export interface BlockNode extends CanvasNodeBase {
  type: 'block'
  data: BlockNodeData
}

export interface ImageNodeData {
  // hydrated at load (workToCanvas) — domain truth lives in WorkDoc.images, keyed by node id
  url: string
  file: string
  snapshot?: SnapshotData
  ar?: number // true source aspect ratio — resizing derives from it so rounding never accumulates
  created_at: string
  tags: string[]
  favorite: boolean
  group: string | null
  description: string
  // transient — never persisted
  isNew?: boolean
}

// At rest the image node is pure layout (`data: {}`); its domain record is WorkDoc.images[id].
export interface ImageNode extends CanvasNodeBase {
  type: 'image'
  data: Partial<ImageNodeData>
}

export type CanvasNode = StationNode | ZoneNode | BlockNode | ImageNode

export interface WorkDoc {
  schema_version: number
  id: string
  title: string
  slug?: string // backend-derived
  created_at?: string // backend-filled
  updated_at?: string // backend-filled
  params: Partial<PanelParams>
  canvas: { viewport: Viewport; nodes: CanvasNode[] }
  snapshots: PersistedSnapshot[]
  images: PersistedImage[]
  stack: PersistedStackItem[]
  preview_image_id: string | null
}

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
