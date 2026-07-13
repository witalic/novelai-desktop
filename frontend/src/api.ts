import type {
  BlocksPage, Catalog, CategoryCount, GenerateParams, GenerateResponse, ImportParseResult,
  ImportSaveResult, LibraryBlock, Preset, PresetParams, StreamEvent, Subscription, TagCount,
  TokenizeRequest, TokenizeResponse, WorkDoc, WorksPage,
} from './types'

export async function saveWork(doc: WorkDoc): Promise<{ id: string; updated_at: string }> {
  const resp = await fetch('/api/vault/works', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(doc),
  })
  if (!resp.ok) {
    let detail = `Save failed (HTTP ${resp.status})`
    try { const b = await resp.json(); if (b?.detail) detail = b.detail } catch { /* non-JSON */ }
    throw new Error(detail)
  }
  return resp.json()
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

export type WorkSort = 'updated' | 'created' | 'name' | 'image_count'
export async function listWorks(
  opts: { page?: number; perPage?: number; search?: string; sort?: WorkSort; direction?: 'asc' | 'desc' } = {},
): Promise<WorksPage> {
  const p = new URLSearchParams({ page: String(opts.page ?? 1), per_page: String(opts.perPage ?? 24) })
  if (opts.search) p.set('search', opts.search)
  if (opts.sort) p.set('sort', opts.sort)
  if (opts.direction) p.set('direction', opts.direction)
  const resp = await fetch(`/api/vault/works?${p}`)
  if (!resp.ok) throw new ApiError(resp.status, `Failed to list works (HTTP ${resp.status})`)
  return resp.json()
}

export async function loadWork(workId: string): Promise<WorkDoc> {
  const resp = await fetch(`/api/vault/works/${workId}`)
  if (!resp.ok) throw new Error(`Failed to load work (HTTP ${resp.status})`)
  return resp.json()
}

export async function deleteWork(workId: string): Promise<void> {
  const resp = await fetch(`/api/vault/works/${encodeURIComponent(workId)}`, { method: 'DELETE' })
  if (!resp.ok) throw new ApiError(resp.status, `Failed to delete work (HTTP ${resp.status})`)
}

// ---- library ----
export type BlockSort = 'updated' | 'created' | 'category'

export async function listBlocks(
  opts: { categories?: string[]; tags?: string[]; search?: string; sort?: BlockSort; page?: number; perPage?: number } = {},
): Promise<BlocksPage> {
  const p = new URLSearchParams()
  for (const c of opts.categories ?? []) p.append('category', c) // repeated param = multi-select
  for (const t of opts.tags ?? []) p.append('tags', t)
  if (opts.search) p.set('search', opts.search)
  if (opts.sort && opts.sort !== 'updated') p.set('sort', opts.sort)
  p.set('page', String(opts.page ?? 1))
  p.set('per_page', String(opts.perPage ?? 48))
  const resp = await fetch(`/api/vault/library/blocks?${p}`)
  if (!resp.ok) throw new ApiError(resp.status, `Failed to list blocks (HTTP ${resp.status})`)
  return resp.json()
}

// Current rows for specific block ids (missing ones omitted) — the prompt widget diffs a pinned
// copy's frozen version against the live block to flag drift.
export async function resolveBlocks(ids: string[]): Promise<LibraryBlock[]> {
  if (!ids.length) return []
  const p = new URLSearchParams()
  for (const id of ids) p.append('ids', id)
  const resp = await fetch(`/api/vault/library/blocks/resolve?${p}`)
  if (!resp.ok) throw new ApiError(resp.status, `Failed to resolve blocks (HTTP ${resp.status})`)
  return resp.json()
}

export async function saveBlock(block: LibraryBlock): Promise<{ id: string }> {
  const resp = await fetch('/api/vault/library/blocks', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(block),
  })
  if (!resp.ok) {
    let detail = `Save failed (HTTP ${resp.status})`
    try { const b = await resp.json(); if (b?.detail) detail = b.detail } catch { /* non-JSON */ }
    throw new ApiError(resp.status, detail)
  }
  return resp.json()
}

export async function deleteBlock(blockId: string): Promise<void> {
  const resp = await fetch(`/api/vault/library/blocks/${blockId}`, { method: 'DELETE' })
  if (!resp.ok) throw new ApiError(resp.status, `Delete failed (HTTP ${resp.status})`)
}

// ---- library import (bulk: parse candidates → save selected) ----
export async function parseImport(payload: { texts?: string[]; zip_b64?: string; path?: string }): Promise<ImportParseResult> {
  return jsonOrThrow(await fetch('/api/vault/library/import/parse', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }), 'Could not read the import')
}
export async function importBlocks(blocks: LibraryBlock[]): Promise<ImportSaveResult> {
  return jsonOrThrow(await fetch('/api/vault/library/import', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ blocks }),
  }), 'Import failed')
}

export async function listCategories(tags: string[] = []): Promise<CategoryCount[]> {
  const p = new URLSearchParams()
  for (const t of tags) p.append('tags', t)
  const qs = p.toString()
  const resp = await fetch(`/api/vault/library/categories${qs ? `?${qs}` : ''}`)
  if (!resp.ok) throw new ApiError(resp.status, `Failed to list categories (HTTP ${resp.status})`)
  return resp.json()
}

// The full built-in category set — the "restore" picker diffs it against the live categories.
export async function defaultCategories(): Promise<{ slug: string; name: string; color: string }[]> {
  return jsonOrThrow(await fetch('/api/vault/library/categories/defaults'), 'Failed to read default categories')
}
export async function restoreCategories(slugs: string[]): Promise<{ restored: string[] }> {
  return jsonOrThrow(await fetch('/api/vault/library/categories/restore', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ slugs }),
  }), 'Restore failed')
}

// Persist a user-defined category order (drag-to-reorder); one order shared by every list.
export async function reorderCategories(slugs: string[]): Promise<{ order: string[] }> {
  return jsonOrThrow(await fetch('/api/vault/library/categories/order', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ slugs }),
  }), 'Reorder failed')
}

export async function saveCategory(name: string, color: string, slug?: string): Promise<{ slug: string; name: string; color: string }> {
  const resp = await fetch('/api/vault/library/categories', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(slug ? { name, color, slug } : { name, color }),
  })
  if (!resp.ok) {
    let detail = `Save failed (HTTP ${resp.status})`
    try { const b = await resp.json(); if (b?.detail) detail = b.detail } catch { /* non-JSON */ }
    throw new ApiError(resp.status, detail)
  }
  return resp.json()
}

export async function deleteCategory(slug: string): Promise<void> {
  const resp = await fetch(`/api/vault/library/categories/${encodeURIComponent(slug)}`, { method: 'DELETE' })
  if (!resp.ok) {
    let detail = `Delete failed (HTTP ${resp.status})`
    try { const b = await resp.json(); if (b?.detail) detail = b.detail } catch { /* non-JSON */ }
    throw new ApiError(resp.status, detail)
  }
}

export interface ExampleImage {
  image_id: string
  work_id: string
  url: string
  favorite: boolean
  group: string | null
  created_at: string
}

export async function listExamples(tags: string[], limit = 8): Promise<ExampleImage[]> {
  const p = new URLSearchParams()
  for (const t of tags) p.append('tags', t)
  p.set('limit', String(limit))
  const resp = await fetch(`/api/vault/library/examples?${p}`)
  if (!resp.ok) throw new ApiError(resp.status, `Failed to load examples (HTTP ${resp.status})`)
  return resp.json()
}

export async function listTags(category = ''): Promise<TagCount[]> {
  const p = category ? `?category=${encodeURIComponent(category)}` : ''
  const resp = await fetch(`/api/vault/library/tags${p}`)
  if (!resp.ok) throw new ApiError(resp.status, `Failed to list tags (HTTP ${resp.status})`)
  return resp.json()
}

// ---- account ----
export async function getSubscription(): Promise<Subscription> {
  return jsonOrThrow(await fetch('/api/account/subscription'), 'Failed to read subscription')
}

// ---- model catalog (static; fetched once and cached by useCatalog) ----
export async function getCatalog(): Promise<Catalog> {
  return jsonOrThrow(await fetch('/api/catalog'), 'Failed to read model catalog')
}

// ---- tokenization (real per-model token counts for the usage indicator) ----
export async function tokenize(req: TokenizeRequest): Promise<TokenizeResponse> {
  return jsonOrThrow(await fetch('/api/tokenize', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(req),
  }), 'Failed to count tokens')
}

// ---- presets (generation-param bundles) ----
export async function listPresets(): Promise<Preset[]> {
  return jsonOrThrow(await fetch('/api/vault/presets'), 'Failed to list presets')
}

export async function savePreset(preset: { id: string; name: string; params: PresetParams }): Promise<{ id: string }> {
  return jsonOrThrow(await fetch('/api/vault/presets', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(preset),
  }), 'Failed to save preset')
}

export async function deletePreset(id: string): Promise<void> {
  await jsonOrThrow(await fetch(`/api/vault/presets/${encodeURIComponent(id)}`, { method: 'DELETE' }), 'Failed to delete preset')
}

export async function setDefaultPreset(id: string): Promise<void> {
  await jsonOrThrow(await fetch('/api/vault/presets/default', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }),
  }), 'Failed to set default preset')
}

export async function setPresetFavorite(id: string, favorite: boolean): Promise<void> {
  await jsonOrThrow(await fetch(`/api/vault/presets/${encodeURIComponent(id)}/favorite`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ favorite }),
  }), 'Failed to update favourite')
}

// Same-origin in production (served at /app/ by FastAPI); proxied to the backend in Vite dev.
export async function generate(params: GenerateParams): Promise<GenerateResponse> {
  const resp = await fetch('/api/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!resp.ok) {
    let detail = `Request failed (HTTP ${resp.status})`
    try {
      const body = await resp.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* non-JSON error body */ }
    throw new Error(detail)
  }
  return resp.json()
}

export interface VaultInfo {
  dir: string
  initialized: boolean
  writable: boolean
  active: boolean
}
export interface VaultConfig {
  active: string | null
  vaults: VaultInfo[]
  proposed_default: string
}
export interface MoveStatus {
  active: boolean
  total: number
  done: number
  error: string | null
}
export interface AppSettings {
  autosave_interval_s: number
  theme: string
  accent: string
  download_dir: string
}

async function jsonOrThrow<T>(resp: Response, fallback: string): Promise<T> {
  if (!resp.ok) {
    let detail = `${fallback} (HTTP ${resp.status})`
    try { const b = await resp.json(); if (b?.detail) detail = b.detail } catch { /* non-JSON */ }
    throw new ApiError(resp.status, detail)
  }
  return resp.json()
}

export async function getVaultConfig(): Promise<VaultConfig> {
  return jsonOrThrow(await fetch('/api/vault/config'), 'Failed to read vault config')
}

export async function addVault(dir: string): Promise<VaultConfig> {
  return jsonOrThrow(await fetch('/api/vault/vaults', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dir }),
  }), 'Failed to add vault')
}

export async function setActiveVault(dir: string): Promise<VaultConfig> {
  return jsonOrThrow(await fetch('/api/vault/active', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dir }),
  }), 'Failed to switch vault')
}

export async function deleteVault(dir: string): Promise<VaultConfig> {
  return jsonOrThrow(await fetch(`/api/vault/vaults?dir=${encodeURIComponent(dir)}`, { method: 'DELETE' }),
    'Failed to delete vault')
}

export async function startMoveVault(src: string, dst: string): Promise<MoveStatus> {
  return jsonOrThrow(await fetch('/api/vault/move', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ src, dst }),
  }), 'Failed to start move')
}

export async function moveStatus(): Promise<MoveStatus> {
  return jsonOrThrow(await fetch('/api/vault/move/status'), 'Failed to read move status')
}

export async function getAppSettings(): Promise<AppSettings> {
  return jsonOrThrow(await fetch('/api/settings'), 'Failed to read settings')
}

export async function patchAppSettings(patch: Partial<AppSettings>): Promise<AppSettings> {
  return jsonOrThrow(await fetch('/api/settings', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
  }), 'Failed to save settings')
}

// NovelAI token — stored in the OS keychain; the value is never returned, only whether it's set.
export interface TokenStatus { set: boolean }

export async function getTokenStatus(): Promise<TokenStatus> {
  return jsonOrThrow(await fetch('/api/settings/novelai-token'), 'Failed to read token status')
}

export async function setNovelaiToken(token: string): Promise<TokenStatus> {
  return jsonOrThrow(await fetch('/api/settings/novelai-token', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }),
  }), 'Failed to save token')
}

export async function clearNovelaiToken(): Promise<TokenStatus> {
  return jsonOrThrow(await fetch('/api/settings/novelai-token', { method: 'DELETE' }), 'Failed to clear token')
}

// Save image(s) to the Downloads folder via the backend (no OS save dialog). `images` are raw base64.
export async function saveDownloads(images: string[]): Promise<{ count: number; dir: string }> {
  const resp = await fetch('/api/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ images }),
  })
  if (!resp.ok) {
    let detail = `Download failed (HTTP ${resp.status})`
    try { const b = await resp.json(); if (b?.detail) detail = b.detail } catch { /* non-JSON */ }
    throw new Error(detail)
  }
  return resp.json()
}

// POST-stream: reads the SSE response incrementally, invoking onEvent per event.
export async function generateStream(
  params: GenerateParams,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch('/api/generate/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
    signal,
  })
  if (!resp.ok || !resp.body) throw new Error(`Stream failed (HTTP ${resp.status})`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true }) // flush multibyte tail on done
      let sep: number
      while ((sep = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        const dataLine = frame.split('\n').find((l) => l.startsWith('data:'))
        // A malformed frame or a throwing handler must not kill the stream (or leak the reader).
        if (dataLine) try { onEvent(JSON.parse(dataLine.slice(5).trim())) } catch { /* skip this event */ }
      }
      if (done) break
    }
  } finally {
    reader.releaseLock() // release even on abort / error so the connection doesn't dangle
  }
}
