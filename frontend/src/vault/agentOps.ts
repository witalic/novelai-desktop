/* Replaying an agent's edits of a work (backend app/agent/changes) over the app's open copy of it — which may hold
 * unsaved edits of its own. Each operation sets what the agent set (the title, a combination, an image's metadata,
 * a gallery block, the cover) or removes what it removed; everything else in the copy stays as the user left it.
 * Replaying an edit twice changes nothing, so overlapping catch-ups are harmless. Pure → tested. */
import type {
  CanvasNode, GalleryBlock, PanelParams, PersistedCombo, PersistedImage, PersistedSnapshot, WorkDoc,
} from '../types'
import { cloneCombos } from './combos'
import { gridsOf, purgeImages, removeIdFromGrids } from './workOps'
import { GALLERY } from './serialize'

export interface ImageFields { tags?: string[]; description?: string; favorite?: boolean }

// The gallery layout, as the backend applies it too (app/agent/gallery.py `apply`) — same operations, same rules.
// `before` names the block a new one lands before (null, or one that is gone: the end). An `order` sets where the
// listed blocks or images stand among each other, in the slots they hold — what the copy has besides keeps its place,
// so a batch replayed again lands the same.
export type GalleryOp =
  | { op: 'gallery_add'; block: GalleryBlock; before: string | null } // a grid comes empty; images follow by place
  | { op: 'gallery_set'; id: string; set: Record<string, unknown> } // a null value removes the field
  | { op: 'gallery_remove'; id: string } // a grid's images go back to Quick access
  | { op: 'gallery_order'; order: string[] } // a move: a section with the blocks under it
  | { op: 'gallery_place'; grid: string; image_ids: string[]; order: string[] } // out of any other grid
  | { op: 'gallery_unplace'; image_ids: string[] } // into Quick access

export type AgentOp =
  | { op: 'title'; title: string }
  | { op: 'combo'; combo: PersistedCombo } // created or edited — the whole combination
  | { op: 'combo_deleted'; id: string }
  | { op: 'image'; id: string; set: ImageFields }
  | { op: 'images_deleted'; ids: string[] }
  | { op: 'images_added'; images: PersistedImage[]; snapshots: PersistedSnapshot[] } // generated, on disk already
  | { op: 'cover'; image_id: string }
  | { op: 'params'; set: Partial<PanelParams>; preset_id?: string } // the station's settings — a preset's, if named
  | GalleryOp

export interface AgentEdits {
  title?: string
  params?: Partial<PanelParams> // the station settings to set (the rest stay as the copy has them)
  presetId?: string // the preset those came from, the last one named
  combos: PersistedCombo[] // the copy's combinations with the agent's applied
  images: Map<string, ImageFields> // per image id, the metadata to set
  deleted: string[] // image ids to remove
  added: { image: PersistedImage; snapshot?: PersistedSnapshot }[] // new gallery images, each with its recipe
  gallery: GalleryOp[] // the layout edits, in order (applyGalleryOps), after the added images joined
  cover?: string // the work's cover — set before the deletions, which move it on if it goes, as the backend does
}

const isGalleryOp = (o: AgentOp): o is GalleryOp => o.op.startsWith('gallery_')
const copyOf = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T

function insertBefore(blocks: GalleryBlock[], items: GalleryBlock[], before: string | null): void {
  const at = before ? blocks.findIndex((b) => b.id === before) : -1
  blocks.splice(at < 0 ? blocks.length : at, 0, ...items)
}

/** The listed items in this order, in the slots they hold; every other item keeps its slot. An item listed but held
 *  twice (a malformed copy) keeps one slot. In place. */
export function reorder<T>(items: T[], order: string[], key: (x: T) => string | undefined): void {
  const listed = new Set(order)
  const present = new Map<string, T>()
  for (const x of items) { const k = key(x); if (k !== undefined && !present.has(k)) present.set(k, x) }
  const queue = [...listed].filter((k) => present.has(k)).map((k) => present.get(k)!)
  const out: T[] = []
  for (const x of items) {
    const k = key(x)
    if (k === undefined || !listed.has(k)) out.push(x)
    else if (queue.length) out.push(queue.shift()!)
  }
  items.splice(0, items.length, ...out)
}

/** Replay layout edits over a gallery's block stack, in place. `inGallery`: whether an image is (still) a gallery
 *  image of the copy — one the user moved out meanwhile stays out. A block that is gone is skipped. */
export function applyGalleryOps(blocks: GalleryBlock[], ops: GalleryOp[], inGallery: (id: string) => boolean): void {
  for (const o of ops) {
    if (o.op === 'gallery_add') {
      if (!blocks.some((b) => b.id === o.block.id)) insertBefore(blocks, [copyOf(o.block)], o.before)
    } else if (o.op === 'gallery_set') {
      const b = blocks.find((x) => x.id === o.id) as Record<string, unknown> | undefined
      if (b) for (const [k, v] of Object.entries(o.set)) { if (v === null) delete b[k]; else b[k] = copyOf(v) }
    } else if (o.op === 'gallery_remove') {
      for (let i = blocks.length - 1; i >= 0; i--) if (blocks[i].id === o.id) blocks.splice(i, 1)
    } else if (o.op === 'gallery_order') {
      reorder(blocks, o.order, (b) => b.id)
    } else if (o.op === 'gallery_place') {
      const grid = gridsOf(blocks).find((g) => g.id === o.grid)
      if (!grid || !Array.isArray(grid.imageIds)) continue // the grid is gone: the images stay where they are
      const ids = [...new Set(o.image_ids)].filter(inGallery)
      for (const g of gridsOf(blocks)) if (g !== grid) for (const id of ids) removeIdFromGrids([g], id)
      for (const id of ids) if (!grid.imageIds.includes(id)) grid.imageIds.push(id)
      reorder(grid.imageIds, o.order, (id) => id)
    } else if (o.op === 'gallery_unplace') {
      for (const id of o.image_ids) removeIdFromGrids(blocks, id)
    }
  }
}

export function planAgentOps(ops: AgentOp[], combos: PersistedCombo[]): AgentEdits {
  let next = cloneCombos(combos)
  const images = new Map<string, ImageFields>()
  const deleted = new Set<string>()
  const added = new Map<string, { image: PersistedImage; snapshot?: PersistedSnapshot }>()
  const gallery: GalleryOp[] = []
  let title: string | undefined
  let cover: string | undefined
  let params: Partial<PanelParams> | undefined
  let presetId: string | undefined
  for (const o of ops) {
    if (o.op === 'title') title = o.title
    else if (o.op === 'cover') cover = o.image_id
    else if (o.op === 'params') { params = { ...params, ...o.set }; presetId = o.preset_id ?? presetId }
    else if (isGalleryOp(o)) gallery.push(o)
    else if (o.op === 'combo') {
      const c = cloneCombos([o.combo])[0]
      const at = next.findIndex((x) => x.id === c.id)
      if (at >= 0) next[at] = c
      else next.unshift(c) // a new one goes on top, as the widget lists them (newest first)
    } else if (o.op === 'combo_deleted') next = next.filter((c) => c.id !== o.id)
    else if (o.op === 'image') images.set(o.id, { ...images.get(o.id), ...o.set })
    else if (o.op === 'images_deleted') for (const id of o.ids) { deleted.add(id); images.delete(id); added.delete(id) }
    else if (o.op === 'images_added') {
      const snaps = new Map(o.snapshots.map((s) => [s.id, s]))
      for (const im of o.images) added.set(im.id, { image: im, snapshot: snaps.get(im.snapshot_id ?? '') })
    }
  }
  return { title, params, presetId, combos: next, images, deleted: [...deleted], added: [...added.values()], gallery, cover }
}

/** Catching up, one run at a time: a call during a run (another agent edit, a save's 409) makes it run once more
 *  — that run may have asked before the newest edit landed — and shares its promise. Resolves true when any run
 *  moved the copy on (`run` says so). */
export function catchUpQueue(run: () => Promise<boolean>): () => Promise<boolean> {
  let running: Promise<boolean> | null = null
  let again = false
  return () => {
    if (running) { again = true; return running }
    running = (async () => {
      let moved = false
      do {
        again = false
        if (await run()) moved = true
      } while (again)
      return moved
    })().finally(() => { running = null })
    return running
  }
}

/** The same edits on a whole WorkDoc, in place (the Works editor's copy). */
export function applyAgentOpsToDoc(doc: WorkDoc, ops: AgentOp[]): void {
  const edits = planAgentOps(ops, doc.combos || [])
  if (edits.title !== undefined) doc.title = edits.title
  if (edits.params) doc.params = { ...doc.params, ...edits.params }
  doc.combos = edits.combos
  const have = new Set(doc.images.map((im) => im.id))
  for (const { image, snapshot } of edits.added) {
    if (have.has(image.id)) continue // replayed twice
    doc.images.push({ ...image })
    if (snapshot && !doc.snapshots.some((s) => s.id === snapshot.id)) doc.snapshots.push(snapshot)
    doc.preview_image_id ||= image.id // as the server set it for a work that had no preview yet
  }
  for (const im of doc.images) {
    const set = edits.images.get(im.id)
    if (set) Object.assign(im, set)
  }
  const inGallery = (id: string) => doc.images.some((im) => im.id === id && im.role === 'gallery')
  if (edits.gallery.length) applyGalleryOps(galleryBlocksOf(doc), edits.gallery, inGallery)
  if (edits.cover && inGallery(edits.cover)) doc.preview_image_id = edits.cover
  if (edits.deleted.length) purgeImages(doc, edits.deleted)
}

// The copy's gallery block stack to edit — a work that never had a gallery zone gets one, as the backend made it.
function galleryBlocksOf(doc: WorkDoc): GalleryBlock[] {
  doc.canvas ??= { viewport: { x: 0, y: 0, zoom: 1 }, nodes: [] }
  doc.canvas.nodes ??= []
  let zone = doc.canvas.nodes.find((n) => n.type === 'zone' && (n.data as { role?: string })?.role === 'gallery')
  if (!zone) { // unplaced — the canvas gives it its place when it opens the work (serialize healAnchors)
    zone = { id: GALLERY, type: 'zone', data: { role: 'gallery', blocks: [] } } as unknown as CanvasNode
    doc.canvas.nodes.push(zone)
  }
  const data = zone.data as { blocks?: GalleryBlock[] }
  return (data.blocks ??= [])
}
