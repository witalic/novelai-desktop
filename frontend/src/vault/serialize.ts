/* Serialize the Vue Flow canvas to a WorkDoc and back. Framework-free (takes plain node arrays)
 * so it is unit-testable. Persistence is an explicit whitelist: domain + layout fields are listed
 * per node type below; everything else on a live node (measured dimensions, selection, transient
 * UI flags like `expanded`/`editing`/`_cw`/`_ch`) never reaches disk. The WHOLE canvas persists —
 * zones define role, not survival: images inside the gallery zone are `role: 'gallery'`, anything
 * loose is `role: 'scratch'` (kept with the work, invisible to galleries/counts/previews). */
import type {
  CanvasNode, GenResult, ImageNodeData, NodeStyle, PanelParams, PersistedCombo, PersistedImage,
  PersistedSnapshot, PersistedStackItem, SnapshotData, Viewport, WorkDoc,
} from '../types'
import { cloneCombos } from './combos'
import { newId } from './ids'
import { DEFAULT_LAYOUT, GAP, type Rect } from '../canvas/widgetGrid'

export const STATION = 'station'
export const LIBRARY = 'library'
export const GALLERY = 'gallery'
export const COMBOS = 'combos'

// The four widgets every work's canvas holds, in the default layout — the skeleton a new work starts from.
export function anchorNodes(): CanvasNode[] {
  const L = DEFAULT_LAYOUT
  const at = (r: Rect) => ({ position: { x: r.x, y: r.y }, style: { width: `${r.w}px`, height: `${r.h}px` }, zIndex: 0 })
  return [
    { id: LIBRARY, type: 'zone', data: { role: 'library' }, ...at(L.library) },
    { id: STATION, type: 'station', data: { ratio: 0.3, axis: 'h', genFirst: true }, ...at(L.station) },
    { id: GALLERY, type: 'zone', data: { role: 'gallery', blocks: [{ id: newId('gb'), type: 'grid', imageIds: [], cols: 3 }] }, ...at(L.gallery) },
    { id: COMBOS, type: 'zone', data: { role: 'combos' }, ...at(L.combos) },
  ]
}

// A work laid out outside the canvas — an agent's holds just its gallery zone, unplaced (backend app/agent/gallery),
// or nothing at all. The widgets it lacks join in the default layout, an unplaced one takes its default place, and
// what each holds stays. A missing gallery comes empty, as the backend makes one, so the agent and the app see the
// same layout. True when it did either: the layout is new, so the host frames it.
export function healAnchors(nodes: CanvasNode[]): boolean {
  const missing: CanvasNode[] = []
  let placed = false
  for (const skeleton of anchorNodes()) {
    const have = nodes.find((n) => n.id === skeleton.id)
    if (!have) missing.push(skeleton.id === GALLERY ? { ...skeleton, data: { role: 'gallery', blocks: [] } } as CanvasNode : skeleton)
    else if (!have.position) {
      Object.assign(have, { position: skeleton.position, style: skeleton.style, zIndex: skeleton.zIndex })
      placed = true
    }
  }
  nodes.unshift(...missing) // anchors first: they parent the content nodes
  return placed || missing.length > 0
}

// The Combinations widget's zone for a work saved before it existed (see workToCanvas): right under its
// Library zone, one module gap below, same width, the default height.
export function combosZoneNode(library?: { position?: { x: number; y: number }; style?: unknown }): CanvasNode {
  const pos = library?.position ?? { x: DEFAULT_LAYOUT.library.x, y: DEFAULT_LAYOUT.library.y }
  const style = (library?.style ?? {}) as NodeStyle
  const libH = Number.parseFloat(style.height || '') || DEFAULT_LAYOUT.library.h
  return {
    id: COMBOS, type: 'zone', position: { x: pos.x, y: pos.y + libH + GAP },
    data: { role: 'combos' }, zIndex: 0,
    style: { width: style.width || `${DEFAULT_LAYOUT.combos.w}px`, height: `${DEFAULT_LAYOUT.combos.h}px` },
  } as CanvasNode
}

// What serialize reads off a live Vue Flow node (or a persisted CanvasNode being re-saved);
// runtime-only fields stay unread. `data` is unknown on purpose: each node type casts to its
// own whitelisted shape below.
export interface LiveNode {
  id: string
  type?: string
  position?: { x: number; y: number }
  parentNode?: string
  style?: unknown
  zIndex?: number
  data?: unknown
}

// ---- persistence whitelists (domain + layout per node type; the rest is transient) ----
const BLOCK_FIELDS = [
  'category', 'name', 'text', 'polarity', 'block_id', 'version', 'tags', // domain (frozen copy + vault ref)
  'frozen', // composition state: kept in the area but excluded from the prompt
  // layout: order in the station list is position.y; loose scratch blocks use position — no extra fields
] as const
const STATION_FIELDS = ['ratio', 'axis', 'genFirst', 'comboId', 'railW'] as const // two-zone layout, rail width + the linked combination
const ZONE_FIELDS = ['role', 'blocks', 'outlineOpen', 'quickOpen', 'railW'] as const // gallery block stack, panel open-state, rail width are layout
// image nodes persist as pure layout — their domain record lives in WorkDoc.images, keyed by node id

function pick(data: Record<string, unknown> | undefined, fields: readonly string[]) {
  const out: Record<string, unknown> = {}
  for (const f of fields) if (data && data[f] !== undefined) out[f] = data[f]
  return out
}

function slim(n: LiveNode): CanvasNode {
  const fields =
    n.type === 'block' ? BLOCK_FIELDS
    : n.type === 'station' ? STATION_FIELDS
    : n.type === 'zone' ? ZONE_FIELDS
    : []
  const data = n.data as Record<string, unknown> | undefined
  let style = n.style as NodeStyle | undefined
  // An expanded block's box is transient UI (`expanded` is stripped) — persist its COLLAPSED size
  // (`_cw`/`_ch`, captured on expand) so it reopens collapsed, not as a collapsed block stranded in
  // an expanded-sized box.
  if (n.type === 'block' && data?.expanded) {
    style = { width: (data._cw as string) || style?.width || '176px', ...(data._ch ? { height: data._ch as string } : {}) }
  }
  return {
    id: n.id, type: n.type, position: n.position, parentNode: n.parentNode,
    style, zIndex: n.zIndex,
    data: pick(data, fields),
  } as CanvasNode
}

// Split a possible data-URL into { file, image_b64 } for persistence.
function splitImage(url: string, file: string) {
  const isData = url.startsWith('data:')
  return { file: isData ? '' : (file || ''), image_b64: isData ? url.replace(/^data:[^,]+,/, '') : null }
}

// `meta.cover` is the work's chosen cover image (its picture in the Works list).
export function canvasToWork(
  nodes: LiveNode[], viewport: Viewport, params: Partial<PanelParams>,
  meta: { id: string; title: string; cover?: string | null }, drafts: GenResult[] = [], favorites: string[] = [],
  combos: PersistedCombo[] = [],
): WorkDoc {
  const station = nodes.find((n) => n.id === STATION)
  const library = nodes.find((n) => n.id === LIBRARY)
  const gallery = nodes.find((n) => n.id === GALLERY)
  const combosZone = nodes.find((n) => n.id === COMBOS)

  // The whole canvas persists: anchors + every block/image node, in or out of a zone.
  const allBlocks = nodes.filter((n) => n.type === 'block')
  const allImages = nodes.filter((n) => n.type === 'image')
  const anchors = [station, library, gallery, combosZone].filter((n): n is LiveNode => !!n)
  const canvasNodes = [...anchors, ...allBlocks, ...allImages].map(slim)

  // Work-level snapshots (the reproducible recipe: prompt composition + params), deduped and shared by
  // gallery images and the draft stack; each references one via snapshot_id. The dedup key includes the
  // params (seed, steps, …), not just the prompt hash — otherwise two images from the same prompt but a
  // different seed collapse onto one snapshot and lose their real recipe.
  const snapshotsByKey = new Map<string, PersistedSnapshot>()
  const snapshotIdOf = (snap: SnapshotData | undefined): string | null => {
    if (!snap?.hash) return null
    const key = `${snap.hash}|${JSON.stringify(snap.params || {})}`
    if (!snapshotsByKey.has(key)) {
      snapshotsByKey.set(key, {
        id: `snap-${snap.hash.length}-${snapshotsByKey.size}-${meta.id.slice(0, 6)}`,
        hash: snap.hash, components: snap.components || [],
        assembled_positive: snap.positive || '', assembled_negative: snap.negative || '',
        params: snap.params || {},
        created_at: snap.created_at || '', // backend stamps this once, on first save
      })
    }
    return snapshotsByKey.get(key)!.id
  }

  const images: PersistedImage[] = allImages.map((im) => {
    const d = (im.data ?? {}) as Partial<ImageNodeData>
    return {
      id: im.id, snapshot_id: snapshotIdOf(d.snapshot),
      role: im.parentNode === GALLERY ? 'gallery' as const : 'scratch' as const,
      ar: d.ar ?? null, // persisted (v2) so the true ratio outlives a lost snapshot
      ...splitImage(d.url || '', d.file || ''),
      created_at: d.created_at || '', // backend fills this if empty
      group: d.group ?? null, favorite: !!d.favorite,
      tags: d.tags || [], description: d.description || '',
    }
  })

  // The generation output pile ("stack") persists too, so reopening a work restores it.
  const stack: PersistedStackItem[] = (drafts || []).map((d) => ({
    id: d.id, snapshot_id: snapshotIdOf(d.snapshot),
    ...splitImage(d.url || '', d.file || ''), created_at: d.created_at || '',
  }))

  // Scratch is working material — a work previews (and counts) only by its gallery images: the chosen cover while it
  // is one, else the first.
  const inGallery = images.filter((im) => im.role === 'gallery')
  const cover = inGallery.find((im) => im.id === meta.cover) ?? inGallery[0]

  return {
    schema_version: 7, id: meta.id, title: meta.title, params, // bump together with models.py + migrate.py
    canvas: { viewport, nodes: canvasNodes },
    snapshots: [...snapshotsByKey.values()],
    images, stack,
    favorites: [...new Set(favorites)], // per-work quick-access set (Library block ids), deduped
    combos: cloneCombos(combos), // saved block sets — a copy, so later edits never reach this document
    preview_image_id: cover?.id ?? null,
  }
}

function restoreSnapshot(snap: PersistedSnapshot | undefined): SnapshotData | undefined {
  return snap
    ? {
        components: snap.components || [], positive: snap.assembled_positive || '',
        negative: snap.assembled_negative || '', params: snap.params || {}, hash: snap.hash || '',
        created_at: snap.created_at || '',
      }
    : undefined
}

// Rebuild the draft stack (GenResult[]) from a saved work's `stack`.
export function workToDrafts(doc: WorkDoc): GenResult[] {
  const snapById = new Map((doc.snapshots || []).map((s) => [s.id, s]))
  return (doc.stack || []).map((s) => {
    const snap = s.snapshot_id ? snapById.get(s.snapshot_id) : undefined
    return {
      id: s.id, file: s.file || '', mock: false,
      url: s.file ? `/api/vault/works/${doc.id}/images/${s.id}` : '',
      params: ((snap?.params) || {}) as unknown as GenResult['params'], snapshot: restoreSnapshot(snap),
      created_at: s.created_at || '', // keep the true generation time across save/reopen cycles
    }
  })
}

// An image node's live data, hydrated from its domain record (WorkDoc.images) and its recipe.
function imageData(workId: string, id: string, im: PersistedImage | undefined, snap: PersistedSnapshot | undefined): ImageNodeData {
  // The persisted ar (v2) wins; params-derived is the v1 fallback. Never re-derive from the rounded node size —
  // that drifts across save/scale cycles.
  const p = (snap?.params || {}) as { width?: number; height?: number }
  return {
    url: im?.file ? `/api/vault/works/${workId}/images/${id}` : '', file: im?.file || '',
    snapshot: restoreSnapshot(snap), // restored into the node so a later re-save preserves it (round-trip fix)
    ar: im?.ar ?? (p.width && p.height ? p.width / p.height : undefined), created_at: im?.created_at || '',
    tags: im?.tags || [], favorite: !!im?.favorite, group: im?.group ?? null, description: im?.description || '',
  }
}

// A gallery image added outside the canvas (an agent's generation) has no node yet: it gets one in the gallery
// zone, in no grid — so it shows in Quick access. Unsized: gallery images render inside the gallery widget, and
// moving one out onto the canvas sizes it then.
export function galleryImageNode(workId: string, im: PersistedImage, snap?: PersistedSnapshot): CanvasNode {
  return {
    id: im.id, type: 'image', parentNode: GALLERY, position: { x: 0, y: 0 }, zIndex: 3,
    data: imageData(workId, im.id, im, snap),
  } as CanvasNode
}

export function workToCanvas(doc: WorkDoc): { nodes: CanvasNode[]; viewport: Viewport } {
  const imageById = new Map((doc.images || []).map((im) => [im.id, im]))
  const snapById = new Map((doc.snapshots || []).map((s) => [s.id, s]))
  const snapOf = (im?: PersistedImage) => (im?.snapshot_id ? snapById.get(im.snapshot_id) : undefined)
  const nodes = (doc.canvas?.nodes || []).map((n): CanvasNode => {
    if (n.type === 'image') {
      const im = imageById.get(n.id)
      return { ...n, data: imageData(doc.id, n.id, im, snapOf(im)) }
    }
    if (n.type === 'block') {
      // A block always opens collapsed (expanded is transient). Drop any persisted height so it
      // sizes to its collapsed content — never a collapsed block stranded in an expanded-sized box
      // (also heals works saved before this was fixed). Width is kept (a real layout choice).
      const style = n.style ? { ...(n.style as NodeStyle) } : undefined
      if (style) delete style.height
      return { ...n, style, data: { ...n.data } } as CanvasNode
    }
    if (n.type === 'zone' && (n.data as { role?: string })?.role === 'gallery') {
      // Heal a gallery zone that never got a block stack (backend migration is authoritative, but a
      // hand-edited work might skip it): seed one album grid owning every gallery image, oldest→newest.
      // Deterministic seed id → stable round-trip. An emptied stack stays empty — that is a layout too.
      const d = { ...(n.data as Record<string, unknown>) }
      const galleryIds = (doc.images || []).filter((im) => im.role === 'gallery')
        .sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || ''))).map((im) => im.id)
      if (!Array.isArray(d.blocks)) {
        d.blocks = [{ id: 'gb-seed', type: 'grid', imageIds: galleryIds, cols: 3 }]
      } else {
        // Prune image ids that no longer exist (deleted/moved-to-scratch) so counts don't drift (M8).
        const live = new Set(galleryIds)
        d.blocks = (d.blocks as { type?: string; imageIds?: string[] }[]).map((b) =>
          b.type === 'grid' && Array.isArray(b.imageIds) ? { ...b, imageIds: b.imageIds.filter((id) => live.has(id)) } : b)
      }
      return { ...n, data: d } as CanvasNode
    }
    return { ...n, data: { ...n.data } } as CanvasNode
  })
  const placed = new Set(nodes.map((n) => n.id))
  for (const im of doc.images || []) {
    if (im.role === 'gallery' && !placed.has(im.id)) nodes.push(galleryImageNode(doc.id, im, snapOf(im)))
  }
  // A work saved before the Combinations widget existed gets its zone, under its Library zone.
  const library = nodes.find((n) => n.id === LIBRARY)
  if (library && !nodes.some((n) => n.id === COMBOS)) nodes.push(combosZoneNode(library))
  return { nodes, viewport: doc.canvas?.viewport || { x: 0, y: 0, zoom: 1 } }
}
