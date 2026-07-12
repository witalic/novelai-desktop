/* Serialize the Vue Flow canvas to a WorkDoc and back. Framework-free (takes plain node arrays)
 * so it is unit-testable. Persistence is an explicit whitelist: domain + layout fields are listed
 * per node type below; everything else on a live node (measured dimensions, selection, transient
 * UI flags like `expanded`/`editing`/`_cw`/`_ch`) never reaches disk. The WHOLE canvas persists —
 * zones define role, not survival: images inside the gallery zone are `role: 'gallery'`, anything
 * loose is `role: 'scratch'` (kept with the work, invisible to galleries/counts/previews). */
import type {
  CanvasNode, GenResult, ImageNodeData, NodeStyle, PanelParams, PersistedImage,
  PersistedSnapshot, PersistedStackItem, SnapshotData, Viewport, WorkDoc,
} from '../types'

export const STATION = 'station'
export const LIBRARY = 'library'
export const GALLERY = 'gallery'
const ANCHORS = new Set([STATION, LIBRARY, GALLERY])

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
  // layout: order in the station list is position.y; loose scratch blocks use position — no extra fields
] as const
const STATION_FIELDS = ['ratio', 'axis', 'genFirst'] as const // two-zone layout (Generation | Composition)
const ZONE_FIELDS = ['role', 'collapsed', 'expandedH'] as const // prompt-widget collapse is layout
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

export function canvasToWork(
  nodes: LiveNode[], viewport: Viewport, params: Partial<PanelParams>,
  meta: { id: string; title: string }, drafts: GenResult[] = [], favorites: string[] = [],
): WorkDoc {
  const station = nodes.find((n) => n.id === STATION)
  const library = nodes.find((n) => n.id === LIBRARY)
  const gallery = nodes.find((n) => n.id === GALLERY)

  // The whole canvas persists: anchors + every block/image node, in or out of a zone.
  const allBlocks = nodes.filter((n) => n.type === 'block')
  const allImages = nodes.filter((n) => n.type === 'image')
  const anchorNodes = [station, library, gallery].filter((n): n is LiveNode => !!n)
  const canvasNodes = [...anchorNodes, ...allBlocks, ...allImages].map(slim)

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

  return {
    schema_version: 4, id: meta.id, title: meta.title, params, // bump together with models.py + migrate.py
    canvas: { viewport, nodes: canvasNodes },
    snapshots: [...snapshotsByKey.values()],
    images, stack,
    favorites: [...new Set(favorites)], // per-work quick-access set (Library block ids), deduped
    // Scratch is working material — a work previews (and counts) only by its gallery images.
    preview_image_id: images.find((im) => im.role === 'gallery')?.id ?? null,
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

export function workToCanvas(doc: WorkDoc): { nodes: CanvasNode[]; viewport: Viewport } {
  const imageById = new Map((doc.images || []).map((im) => [im.id, im]))
  const snapById = new Map((doc.snapshots || []).map((s) => [s.id, s]))
  const nodes = (doc.canvas?.nodes || []).map((n): CanvasNode => {
    if (n.type === 'image') {
      const im = imageById.get(n.id)
      const snap = im?.snapshot_id ? snapById.get(im.snapshot_id) : undefined
      const url = im?.file ? `/api/vault/works/${doc.id}/images/${n.id}` : ''
      // Restore the snapshot into the node so a later re-save preserves it (round-trip fix).
      const snapshot = restoreSnapshot(snap)
      // The persisted ar (v2) wins; params-derived is the v1 fallback. Never re-derive from the
      // rounded node size — that drifts across save/scale cycles.
      const p = (snap?.params || {}) as { width?: number; height?: number }
      const ar = im?.ar ?? (p.width && p.height ? p.width / p.height : undefined)
      return {
        ...n,
        data: {
          url, file: im?.file || '', snapshot, ar, created_at: im?.created_at || '',
          tags: im?.tags || [], favorite: !!im?.favorite, group: im?.group ?? null, description: im?.description || '',
        },
      }
    }
    if (n.type === 'block') {
      // A block always opens collapsed (expanded is transient). Drop any persisted height so it
      // sizes to its collapsed content — never a collapsed block stranded in an expanded-sized box
      // (also heals works saved before this was fixed). Width is kept (a real layout choice).
      const style = n.style ? { ...(n.style as NodeStyle) } : undefined
      if (style) delete style.height
      return { ...n, style, data: { ...n.data } } as CanvasNode
    }
    return { ...n, data: { ...n.data } } as CanvasNode
  })
  return { nodes, viewport: doc.canvas?.viewport || { x: 0, y: 0, zoom: 1 } }
}

export function isAnchor(id: string) {
  return ANCHORS.has(id)
}
