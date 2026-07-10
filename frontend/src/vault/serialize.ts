/* Serialize the Vue Flow canvas to a WorkDoc and back. Framework-free (takes plain node arrays)
 * so it is unit-testable. Only content inside the anchor zones is saved; drafts are dropped. */
/* eslint-disable @typescript-eslint/no-explicit-any */

export const STATION = 'station'
export const LIBRARY = 'library'
export const GALLERY = 'gallery'
const ANCHORS = new Set([STATION, LIBRARY, GALLERY])

function slim(n: any) {
  const data = n.type === 'image' ? {} : n.data
  return { id: n.id, type: n.type, position: n.position, parentNode: n.parentNode, style: n.style, data }
}

// Split a possible data-URL into { file, image_b64 } for persistence.
function splitImage(url: string, file: string) {
  const isData = url.startsWith('data:')
  return { file: isData ? '' : (file || ''), image_b64: isData ? url.replace(/^data:[^,]+,/, '') : null }
}

export function canvasToWork(nodes: any[], viewport: any, params: any, meta: { id: string; title: string }, drafts: any[] = []) {
  const station = nodes.find((n) => n.id === STATION)
  const library = nodes.find((n) => n.id === LIBRARY)
  const gallery = nodes.find((n) => n.id === GALLERY)

  // Saved nodes: the anchors + blocks inside station/library + images inside gallery (drafts excluded).
  const savedBlocks = nodes.filter((n) => n.type === 'block' && (n.parentNode === STATION || n.parentNode === LIBRARY))
  const galleryImages = nodes.filter((n) => n.type === 'image' && n.parentNode === GALLERY)
  const anchorNodes = [station, library, gallery].filter(Boolean)
  const canvasNodes = [...anchorNodes, ...savedBlocks, ...galleryImages].map(slim)

  // Work-level snapshots (the reproducible recipe: prompt composition + params), deduped and shared by
  // gallery images and the draft stack; each references one via snapshot_id. The dedup key includes the
  // params (seed, steps, …), not just the prompt hash — otherwise two images from the same prompt but a
  // different seed collapse onto one snapshot and lose their real recipe.
  const snapshotsByKey = new Map<string, any>()
  const snapshotIdOf = (snap: any): string | null => {
    if (!snap?.hash) return null
    const key = `${snap.hash}|${JSON.stringify(snap.params || {})}`
    if (!snapshotsByKey.has(key)) {
      snapshotsByKey.set(key, {
        id: `snap-${snap.hash.length}-${snapshotsByKey.size}-${meta.id.slice(0, 6)}`,
        hash: snap.hash, components: snap.components || [],
        assembled_positive: snap.positive || '', assembled_negative: snap.negative || '',
        params: snap.params || {},
      })
    }
    return snapshotsByKey.get(key).id
  }

  const images = galleryImages.map((im) => ({
    id: im.id, snapshot_id: snapshotIdOf(im.data?.snapshot),
    ...splitImage(im.data?.url || '', im.data?.file || ''),
    created_at: im.data?.created_at || '', // backend fills this if empty
    group: im.data?.group || null, favorite: !!im.data?.favorite,
    tags: im.data?.tags || [], description: im.data?.description || '',
  }))

  // The generation output pile ("stack") persists too, so reopening a work restores it.
  const stack = (drafts || []).map((d) => ({
    id: d.id, snapshot_id: snapshotIdOf(d.snapshot),
    ...splitImage(d.url || '', d.file || ''), created_at: d.created_at || '',
  }))

  return {
    schema_version: 1, id: meta.id, title: meta.title, params,
    canvas: { viewport, nodes: canvasNodes },
    snapshots: [...snapshotsByKey.values()],
    images, stack,
    preview_image_id: images.length ? images[0].id : null,
  }
}

function restoreSnapshot(snap: any) {
  return snap
    ? { components: snap.components || [], positive: snap.assembled_positive || '', negative: snap.assembled_negative || '', params: snap.params || {}, hash: snap.hash || '' }
    : undefined
}

// Rebuild the draft stack (GenResult[]) from a saved work's `stack`.
export function workToDrafts(doc: any): any[] {
  const snapById = new Map<string, any>((doc.snapshots || []).map((s: any) => [s.id, s]))
  return (doc.stack || []).map((s: any) => {
    const snap = s.snapshot_id ? snapById.get(s.snapshot_id) : null
    return {
      id: s.id, file: s.file || '', mock: false,
      url: s.file ? `/api/vault/works/${doc.id}/images/${s.id}` : '',
      params: (snap?.params) || {}, snapshot: restoreSnapshot(snap),
    }
  })
}

export function workToCanvas(doc: any) {
  const imageById = new Map<string, any>((doc.images || []).map((im: any) => [im.id, im]))
  const snapById = new Map<string, any>((doc.snapshots || []).map((s: any) => [s.id, s]))
  const nodes = (doc.canvas?.nodes || []).map((n: any) => {
    if (n.type === 'image') {
      const im = imageById.get(n.id)
      const snap = im?.snapshot_id ? snapById.get(im.snapshot_id) : null
      const url = im?.file ? `/api/vault/works/${doc.id}/images/${n.id}` : ''
      // Restore the snapshot into the node so a later re-save preserves it (round-trip fix).
      const snapshot = restoreSnapshot(snap)
      return {
        ...n,
        data: {
          url, file: im?.file || '', snapshot, created_at: im?.created_at || '',
          tags: im?.tags || [], favorite: !!im?.favorite, group: im?.group || null, description: im?.description || '',
        },
      }
    }
    return { ...n, data: { ...n.data } }
  })
  return { nodes, viewport: doc.canvas?.viewport || { x: 0, y: 0, zoom: 1 } }
}

export function isAnchor(id: string) {
  return ANCHORS.has(id)
}
