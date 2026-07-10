<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { VueFlow, useVueFlow, type Node as FlowNode } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { NodeResizer } from '@vue-flow/node-resizer'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/node-resizer/dist/style.css'
import { getVaultConfig, saveDownloads } from '../api'
import { useToast } from '../composables/useToast'
import { useConfirm } from '../composables/useConfirm'
import { useImagePipeline, PICK_SCALES } from '../composables/useImagePipeline'
import { useAutosave } from '../composables/useAutosave'
import { workToCanvas, GALLERY, LIBRARY, STATION } from '../vault/serialize'
import { insertionIndex, packColumn, packedHeight, PACK_X } from '../canvas/pack'
import PromptWidget from './PromptWidget.vue'
import { newId } from '../vault/ids'
import { onBeforeQuit } from '../electron'
import type { GenResult, LibraryBlock, PanelParams, PersistedComponent, SnapshotData, WorkDoc } from '../types'

const toast = useToast()
const { confirm } = useConfirm()
const props = defineProps<{
  drafts: GenResult[]; busy: boolean; error: string; preview: string; params: PanelParams
  openWork: WorkDoc | null; insertBlocks?: { blocks: LibraryBlock[]; nonce: number } | null
}>()
const emit = defineEmits<{
  generate: [{ positive: string; negative: string; snapshot: SnapshotData }]
  take: []
  cancel: []
  saved: [string]
  navigate: [string]
}>()

const {
  nodes, addNodes, removeNodes, findNode, onNodeDragStop, getIntersectingNodes, viewport, screenToFlowCoordinate,
  setNodes, setViewport, onNodeContextMenu, onSelectionContextMenu, onPaneContextMenu,
} = useVueFlow()

// The "station" is one coupled node: [ Output | Positive / Negative lanes ] + header + meta.
// Internal areas are ratio-driven (data.outputRatio, data.posRatio) so they scale on resize + splitters.
// STATION/LIBRARY/GALLERY zone ids come from serialize (single source of truth). Content inside them is
// saved; anything loose on the canvas is a draft.
const HEADER = 44
const META = 66
const ANCHORS = new Set([STATION, LIBRARY, GALLERY])

const CATS: Record<string, string> = {
  style: '#6e5dc6', character: '#0c66e4', pose: '#ae4787', environment: '#1f845a',
  lighting: '#b65c02', camera: '#12b5a6', outfit: '#d4537e', negative: '#e2483d', custom: '#738496',
}
const catColor = (c: string) => CATS[c] ?? CATS.custom

const compBlocks = computed(() => nodes.value.filter((n) => n.type === 'block' && n.parentNode === STATION))
const composed = computed(() => {
  const pick = (neg: boolean) => compBlocks.value
    .filter((b) => (b.data.polarity === 'negative') === neg)
    .slice().sort((a, b) => a.position.x - b.position.x)
    .map((b) => String(b.data.text || '').trim()).filter(Boolean)
  return { positive: pick(false).join(', '), negative: pick(true).join(', ') }
})
const tokenEstimate = computed(() => Math.ceil((composed.value.positive.length + composed.value.negative.length) / 4))
// Loose blocks/images not inside any anchor zone are scratch — saved with the work, but with no
// role in generation or galleries.
const scratchCount = computed(() => nodes.value.filter((n) => (n.type === 'block' || n.type === 'image') && !ANCHORS.has(n.parentNode ?? '')).length)
const childCount = (id: string) => nodes.value.filter((n) => n.parentNode === id).length

const flowRef = ref<HTMLElement | null>(null)
const topSelected = ref(false)

// Image-rendering pipeline (decode sizing, ?w= thumbnails, flash-free swap, reference scaling). `dims` and
// `sizeOf` are hoisted node-size helpers shared with arrange/settle; the pipeline gets them by reference.
const { shownSrc, scaleOf, imgScale, fullStyle, imgSrc, seedSrc, swapSrc, applyScale } =
  useImagePipeline({ nodes, findNode, sizeOf, dims })

// Vault autosave (dirty flag, flush, save state). Owns the state + logic; the lifecycle (window listeners,
// the periodic timer, and the KeepAlive activate/deactivate hooks) is wired in onMounted/onUnmounted below.
const { title, vaultReady, workId, saveState, savedAt, markDirty, flush, flushIfDirty, manualSave,
  onBeforeUnload, refreshInterval, stopAutosave, resetBaseline } = useAutosave({
  nodes, viewport, params: () => props.params, drafts: () => props.drafts,
  onNoVault: () => emit('navigate', 'settings'), onSaved: rewriteSavedUrls,
})
// After a save, point kept gallery images at their on-disk vault URL so later saves don't re-serialize their
// base64 (a 30-image work would otherwise ship hundreds of MB per flush). Display is unaffected — the shown
// src (shownSrc) still holds the data: URL until the next scale swaps in the sized thumbnail.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function rewriteSavedUrls(wid: string) {
  for (const n of nodes.value as any[]) {
    // Every image node persists now (gallery AND scratch), so every just-saved data: URL repoints.
    if (n.type === 'image' && typeof n.data?.url === 'string' && n.data.url.startsWith('data:')) {
      n.data = { ...n.data, url: `/api/vault/works/${wid}/images/${n.id}`, file: `images/${n.id}.png` }
    }
  }
  // The draft stack lives in GenerateView — emit synchronously so it repoints its data: URLs within the same
  // dirty-suppression window (the stack, up to 50 images, would otherwise re-serialize as base64 each save).
  emit('saved', wid)
}

function toFlow(clientX: number, clientY: number) {
  if (typeof screenToFlowCoordinate === 'function') return screenToFlowCoordinate({ x: clientX, y: clientY })
  const rect = flowRef.value?.getBoundingClientRect()
  const vp = viewport.value
  return { x: (clientX - (rect?.left ?? 0) - vp.x) / vp.zoom, y: (clientY - (rect?.top ?? 0) - vp.y) / vp.zoom }
}

// Drag the top-of-stack image out of the output slot onto the canvas to keep it (materialise a node).
function onDraftDragStart(e: DragEvent) {
  if (!props.drafts.length || !e.dataTransfer) return
  e.dataTransfer.effectAllowed = 'move'
  e.dataTransfer.setData('text/plain', 'nai-draft')
}
function onCanvasDrop(e: DragEvent) {
  e.preventDefault()
  if (e.dataTransfer?.getData('text/plain') !== 'nai-draft') return // only our own draft-drag materialises (not an external file/image)
  const draft = props.drafts[0]
  if (!draft) return
  const pos = toFlow(e.clientX, e.clientY)
  const ar = (draft.params.width || 832) / (draft.params.height || 1216)
  const w = ar >= 1 ? 180 : Math.round(180 * ar)
  const h = ar >= 1 ? Math.round(180 / ar) : 180
  const id = draft.id // reuse the draft's id/file so a restored draft keeps its image when kept
  // The generation recipe (prompt + params) lives in the snapshot; the node keeps only display + meta.
  // `ar` is the true source aspect ratio — resizing derives sizes from it so rounding never accumulates.
  // Keep the draft's original generation time when it has one (restored stacks) — materialising is not creating.
  const data = { url: draft.url, file: draft.file || '', snapshot: draft.snapshot, ar, created_at: draft.created_at || new Date().toISOString() }
  // Dropping straight onto the Gallery keeps the image there immediately — no bounce via the canvas first.
  const gal = findNode(GALLERY)
  const gd = gal ? dims(gal) : { w: 0, h: 0 }
  const gp = gal ? (gal.computedPosition || gal.position) : { x: 0, y: 0 } // top-level → computedPosition == position
  const inGallery = !!gal && pos.x >= gp.x && pos.x <= gp.x + gd.w && pos.y >= gp.y && pos.y <= gp.y + gd.h
  if (inGallery && gal) {
    addNodes([{ id, type: 'image', parentNode: GALLERY, zIndex: 3, style: { width: `${w}px`, height: `${h}px` },
      position: { x: pos.x - gp.x - w / 2, y: pos.y - gp.y - h / 2 }, data }])
    nudgeById(id)
  } else {
    addNodes([{ id, type: 'image', position: { x: pos.x - w / 2, y: pos.y - h / 2 }, zIndex: 3,
      style: { width: `${w}px`, height: `${h}px` }, data }])
  }
  seedSrc(id) // record the shown src so a later scale can swap without blanking
  emit('take')
}

// Shift an image right until it no longer overlaps a sibling image (same parent), using real sizes.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function dims(n: any) {
  return { w: n.dimensions?.width || Number.parseFloat(n.style?.width) || 116, h: n.dimensions?.height || Number.parseFloat(n.style?.height) || 168 }
}
// Size straight from `style` — authoritative right after applyScale, before Vue Flow re-measures
// dimensions (which is async). Using it in arrange avoids packing with stale sizes → no overlap.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function sizeOf(n: any) {
  return { w: Number.parseFloat(n.style?.width) || dims(n).w, h: Number.parseFloat(n.style?.height) || dims(n).h }
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function nudgeIfOverlapping(live: any) {
  const { w: lw, h: lh } = dims(live)
  const others = nodes.value.filter((n) => n.type === 'image' && n.id !== live.id && n.parentNode === live.parentNode)
  const pos = { x: live.position.x, y: live.position.y }
  const hits = (p: { x: number; y: number }) => others.some((o) => {
    const { w, h } = dims(o)
    return Math.abs((o.position.x + w / 2) - (p.x + lw / 2)) < (w + lw) / 2 - 2
      && Math.abs((o.position.y + h / 2) - (p.y + lh / 2)) < (h + lh) / 2 - 2
  })
  let guard = 0
  while (guard < 80 && hits(pos)) { pos.x += 16; guard += 1 }
  live.position = pos
}
function nudgeById(id: string) {
  const live = findNode(id)
  if (live) nudgeIfOverlapping(live)
}

// ---- grid arrange (reference scaling + the image pipeline live in useImagePipeline) ----
// Grid-arrange a set of images with equal gaps, keeping reading order. Anchoring: inside a zone,
// pack from the zone's top-left; loose on the canvas, pack from the set's own top-left (or a stable
// captured anchor during a resize) so nothing jumps to the origin.
type Anchor = { x: number; y: number }
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function arrangeImages(imgs: any[], anchors?: Map<string, Anchor>) {
  const gap = 12
  const byParent = new Map<string, any[]>()
  for (const im of imgs) {
    const live = findNode(im.id)
    if (!live || live.type !== 'image') continue
    const p = live.parentNode ?? ''
    if (!byParent.has(p)) byParent.set(p, [])
    byParent.get(p)!.push(live)
  }
  for (const [p, list] of byParent) {
    const parent = p ? findNode(p) : null
    const maxW = Math.max(...list.map((n) => sizeOf(n).w))
    let baseX: number, baseY: number, rightLimit: number
    if (parent && parent.type === 'zone') {
      baseX = gap; baseY = 44; rightLimit = dims(parent).w - gap // fill the zone from below its header
    } else {
      const a = anchors?.get(p) ?? { x: Math.min(...list.map((n) => n.position.x)), y: Math.min(...list.map((n) => n.position.y)) }
      baseX = a.x; baseY = a.y; rightLimit = a.x + Math.ceil(Math.sqrt(list.length)) * (maxW + gap)
    }
    list.sort((a, b) => (a.position.y - b.position.y) || (a.position.x - b.position.x))
    let x = baseX, y = baseY, rowH = 0
    for (const im of list) {
      const d = sizeOf(im)
      if (x > baseX && x + d.w > rightLimit) { x = baseX; y += rowH + gap; rowH = 0 }
      im.position = { x, y }
      x += d.w + gap
      rowH = Math.max(rowH, d.h)
    }
  }
}

// Clickable scale picker under an all-images selection (up to ×5). Screen-space box of the selected
// images + whether every selected node is an image; reactive to selection, sizes and the viewport.
const scalePicker = computed(() => {
  const sel = nodes.value.filter((n) => n.selected)
  if (!sel.length || !sel.every((n) => n.type === 'image')) return null
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const n of sel) {
    const cp = n.computedPosition || n.position
    const d = dims(n)
    minX = Math.min(minX, cp.x); minY = Math.min(minY, cp.y)
    maxX = Math.max(maxX, cp.x + d.w); maxY = Math.max(maxY, cp.y + d.h)
  }
  const vp = viewport.value
  const scales = sel.map((n) => scaleOf(n))
  const current = scales.every((s) => s === scales[0]) ? scales[0] : null
  return { x: (minX + maxX) / 2 * vp.zoom + vp.x, y: maxY * vp.zoom + vp.y + 8, current }
})
// Set every selected image to a reference scale and re-arrange (anchored at the set's current top-left).
function applyScaleAll(scale: number) {
  const imgs = nodes.value.filter((n) => n.type === 'image' && n.selected)
  if (!imgs.length) return
  const anchors = new Map<string, Anchor>()
  for (const im of imgs) {
    const p = im.parentNode ?? ''
    const cur = anchors.get(p)
    anchors.set(p, cur ? { x: Math.min(cur.x, im.position.x), y: Math.min(cur.y, im.position.y) } : { x: im.position.x, y: im.position.y })
  }
  imgs.forEach((t) => applyScale(t, scale))
  if (imgs.length > 1) arrangeImages(imgs, anchors)
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

// Reflow the station's blocks so each keeps its relative position WITHIN its polarity lane after the
// station resizes or a splitter moves — a negative block never drifts into the positive region.
function relayoutBlocks() {
  const st = findNode(STATION)
  if (!st) return
  // dims() falls back to the node's style size, so this is safe even when the station is unmounted by
  // viewport culling (its measured `dimensions` may be absent) or not yet measured after load.
  const { w: stW, h } = dims(st)
  const outputW = (st.data.outputRatio ?? 0.3) * stW
  const compW = stW - outputW
  const laneBoundary = HEADER + (st.data.posRatio ?? 0.5) * (h - HEADER - META)
  for (const n of nodes.value) {
    if (n.type !== 'block' || n.parentNode !== STATION) continue
    const neg = n.data.polarity === 'negative'
    const laneTop = neg ? laneBoundary : HEADER
    const laneBot = neg ? (h - META) : laneBoundary
    const bd = dims(n)
    n.position = {
      x: outputW + (n.data.xFrac ?? 0.06) * Math.max(0, compW - bd.w),
      y: laneTop + (n.data.laneFrac ?? 0.12) * Math.max(0, (laneBot - laneTop) - bd.h),
    }
  }
}

// The three anchor zones only — the skeleton every work starts from.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function zoneNodes(): any[] {
  return [
    { id: LIBRARY, type: 'zone', position: { x: 40, y: 40 }, data: { role: 'library' }, zIndex: 0, style: { width: '244px', height: '440px' } },
    { id: STATION, type: 'station', position: { x: 316, y: 40 }, data: { outputRatio: 0.3, posRatio: 0.5 }, zIndex: 0, style: { width: '760px', height: '460px' } },
    { id: GALLERY, type: 'zone', position: { x: 1108, y: 40 }, data: { role: 'gallery' }, zIndex: 0, style: { width: '320px', height: '440px' } },
  ]
}

// Start a fresh work: persist the current one, then reset in a SINGLE setNodes to the anchor zones.
// (Never clear to an empty graph — that detaches Vue Flow's drag/zoom handlers and locks the canvas.)
async function newWork() {
  // Persist the current work first; if the save fails, confirm before wiping — never discard silently.
  if (!(await flush(true))) {
    const proceed = await confirm({
      title: 'Discard unsaved changes?', danger: true, confirmLabel: 'Discard & new',
      message: "The current work couldn't be saved. Start a new work anyway? Unsaved changes will be lost.",
    })
    if (!proceed) return
  }
  workId.value = newId('work')
  title.value = ''
  setNodes(zoneNodes())
  shownSrc.value = {} // no images in a fresh work — drop the previous work's entries
  setViewport({ x: 40, y: 40, zoom: 0.7 })
  resetBaseline('idle')
}

onMounted(async () => {
  await checkVault()
  if (props.openWork) loadDoc(props.openWork)
  else addNodes(zoneNodes()) // fresh start → empty anchor zones (no demo blocks; the Library holds those)
})
watch(() => props.openWork, (w) => { if (w) loadDoc(w) })

// Settle EVERY dragged node (multi-select moves a whole set) — not just the grabbed one.
onNodeDragStop(({ nodes: dragged, node }) => {
  const set = dragged && dragged.length ? dragged : [node]
  for (const n of set) settleNode(n)
})

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function settleNode(node: any) {
  const live = findNode(node.id)
  if (!live) return
  const st = findNode(STATION)
  const stPos = st ? (st.computedPosition || st.position) : { x: 0, y: 0 } // top-level → computedPosition == position
  const rel = st ? { x: node.computedPosition.x - stPos.x, y: node.computedPosition.y - stPos.y } : { x: 0, y: 0 }
  const overStation = getIntersectingNodes(node).some((n) => n.id === STATION)
  const { w: stW, h } = st ? dims(st) : { w: 824, h: 460 } // dims() → style fallback, cull-safe
  const outputW = (st?.data.outputRatio ?? 0.3) * stW
  const laneBoundary = HEADER + (st?.data.posRatio ?? 0.5) * (h - HEADER - META)

  if (node.type === 'block') {
    // Blocks belong only in the composition side (right of the Output column).
    if (overStation && rel.x >= outputW && rel.y >= HEADER && rel.y <= h - META) {
      if (live.parentNode !== STATION) live.position = rel
      live.parentNode = STATION
      live.data.polarity = live.position.y < laneBoundary ? 'positive' : 'negative'
      const neg = live.data.polarity === 'negative'
      const lTop = neg ? laneBoundary : HEADER
      const lBot = neg ? (h - META) : laneBoundary
      const bd = dims(live)
      live.data.xFrac = clamp01((live.position.x - outputW) / Math.max(1, (stW - outputW) - bd.w))
      live.data.laneFrac = clamp01((live.position.y - lTop) / Math.max(1, (lBot - lTop) - bd.h))
    } else {
      // Not in the composition — magnetic drop into the quick-access palette, or detach to scratch.
      const lib = getIntersectingNodes(node).find((n) => n.id === LIBRARY)
      if (lib) {
        const dropY = node.computedPosition.y - lib.computedPosition.y
        const others = nodes.value
          .filter((n) => n.type === 'block' && n.parentNode === LIBRARY && n.id !== live.id)
          .sort((a, b) => a.position.y - b.position.y)
        const slot = insertionIndex(others.map((o) => ({ y: o.position.y, h: blockH(o) })), dropY)
        live.parentNode = LIBRARY
        const order = others.map((o) => o.id)
        order.splice(slot, 0, live.id)
        repackLibrary(order) // snaps the drop into the column — no free placement inside the widget
      } else if (live.parentNode) {
        live.position = { x: node.computedPosition.x, y: node.computedPosition.y }
        const fromLibrary = live.parentNode === LIBRARY
        live.parentNode = undefined
        if (fromLibrary) repackLibrary() // close the gap the departed block left
      }
    }
  } else if (node.type === 'image') {
    const gal = getIntersectingNodes(node).find((n) => n.type === 'zone' && n.data.role === 'gallery')
    if (gal) {
      const gp = gal.computedPosition
      live.parentNode = gal.id
      live.position = { x: node.computedPosition.x - gp.x, y: node.computedPosition.y - gp.y }
    } else if (live.parentNode) {
      live.position = { x: node.computedPosition.x, y: node.computedPosition.y }
      live.parentNode = undefined
    }
    nudgeIfOverlapping(live)
  }
}

// ---- prompt widget (library zone): magnetic column packing + collapse ----
// The palette's layout IS its order (design/prompt-widget-mockup.html): children pack into a
// single column; a drop picks its slot by y. `order` (ids) overrides the y-derived order when the
// caller has just computed an insertion slot.
// Style-first (like sizeOf): authoritative right after expand/collapse/resize mutations, before
// Vue Flow re-measures `dimensions` (which is async — packing with it would use stale heights).
const blockH = (n: any) => Number.parseFloat(n.style?.height) || n.dimensions?.height || 34
function repackLibrary(order?: string[]) {
  const zone = findNode(LIBRARY)
  if (!zone) return
  const children = nodes.value.filter((n) => n.type === 'block' && n.parentNode === LIBRARY)
  const sorted = order
    ? order.map((id) => children.find((c) => c.id === id)).filter((c): c is any => !!c)
    : children.slice().sort((a, b) => a.position.y - b.position.y)
  const rowW = dims(zone).w - PACK_X * 2
  const items = sorted.map((c) => ({ id: c.id, h: blockH(c) }))
  for (const p of packColumn(items)) {
    const live = findNode(p.id)!
    live.position = { x: p.x, y: p.y }
    live.style = { ...(live.style as object), width: `${rowW}px` }
  }
  // Auto-grow the zone so the column always fits (never shrink — the user owns the zone size).
  const need = packedHeight(items)
  if (!zone.data.collapsed && need > dims(zone).h) zone.style = { ...(zone.style as object), height: `${need}px` }
}

// Collapse persists with the work (layout layer); the runtime `hidden` flag on children does not.
function toggleLibraryCollapse() {
  const zone = findNode(LIBRARY)
  if (!zone) return
  const collapsed = !zone.data.collapsed
  if (collapsed) {
    zone.data.expandedH = dims(zone).h
    zone.style = { ...(zone.style as object), height: '38px' }
  } else {
    zone.style = { ...(zone.style as object), height: `${zone.data.expandedH || 440}px` }
  }
  zone.data.collapsed = collapsed
  syncLibraryHidden()
}
function syncLibraryHidden() {
  const hide = !!findNode(LIBRARY)?.data.collapsed
  for (const n of nodes.value) if (n.parentNode === LIBRARY) n.hidden = hide
}

// A block "used" from the Library tab lands in the canvas Library zone, keeping its vault link
// (block_id/version/tags) so generated snapshots and inherited image tags stay traceable.
function insertLibraryBlock(b: LibraryBlock) {
  addNodes([{
    // Unique node id (not a per-mount counter) so re-inserting a block into a reloaded work can't collide.
    id: newId(`lib-${b.id}`), type: 'block', parentNode: LIBRARY, zIndex: 2,
    position: { x: PACK_X, y: 1e6 }, style: { width: '176px' }, // y sorts it last; repack sets the real slot
    data: {
      category: b.category, name: b.name, text: b.text, polarity: b.polarity, expanded: false,
      block_id: b.id, version: b.version ?? 1, tags: b.tags ?? [],
    },
  }])
  repackLibrary()
}
watch(() => props.insertBlocks?.nonce, () => { props.insertBlocks?.blocks.forEach(insertLibraryBlock) })
function doGenerate() {
  const components = compBlocks.value.slice().sort((a, b) => a.position.x - b.position.x).map((b): PersistedComponent => ({
    source: b.data.block_id ? 'library' : 'custom', block_id: b.data.block_id, version: b.data.version,
    name: b.data.name, text: String(b.data.text || '').trim(), polarity: b.data.polarity,
    category: b.data.category, tags: b.data.tags || [],
  }))
  const c = composed.value
  const snapshot: SnapshotData = {
    components, positive: c.positive, negative: c.negative, params: { ...props.params },
    hash: components.map((x) => `${x.polarity}:${x.text}`).join('|'),
  }
  emit('generate', { positive: c.positive, negative: c.negative, snapshot })
}

// ---- vault: title/save state + logic live in useAutosave (above). Keyboard shortcuts below. ----
function onKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); manualSave(); return }
  if (e.key === 'Delete' || e.key === 'Backspace') {
    const t = e.target as HTMLElement | null
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return // typing, not deleting
    const ids = nodes.value.filter((n) => n.selected && REMOVABLE.has(n.type)).map((n) => n.id)
    if (ids.length) { e.preventDefault(); removeNodes(ids) }
  }
}

// Mark the work dirty on any change to the canvas / params / drafts / title (see useAutosave).
watch([nodes, () => props.params, () => props.drafts, title], markDirty, { deep: true })

// Re-read whether a vault is configured. Under KeepAlive onMounted runs once, so a vault added in Settings
// mid-session would otherwise leave `vaultReady` false forever → autosave silently dead (H7).
async function checkVault() {
  try { vaultReady.value = !!(await getVaultConfig()).active } catch { /* backend not ready */ }
}

// Lifecycle. The window/quit listeners must be live whenever the app is open, so they sit in onMounted. The
// keydown handler (Ctrl+S / Delete) is scoped to onActivated/onDeactivated: CanvasBoard stays mounted under
// KeepAlive, so a global Delete would otherwise remove canvas nodes while another view is showing (H8).
let disposeBeforeQuit: (() => void) | null = null
onMounted(() => {
  window.addEventListener('beforeunload', onBeforeUnload)
  disposeBeforeQuit = onBeforeQuit(async () => { await flush() }) // Electron waits for the full save before quitting
})
onUnmounted(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
  window.removeEventListener('keydown', onKeydown) // safety if destroyed while active
  disposeBeforeQuit?.()
  activeSplitCleanup?.() // tear down splitter drag listeners if we unmount mid-drag
  stopAutosave()
})
onActivated(() => {
  window.addEventListener('keydown', onKeydown)
  checkVault()      // a vault may have been configured while we were away
  refreshInterval() // pick up a changed autosave interval
})
onDeactivated(() => {
  window.removeEventListener('keydown', onKeydown)
  flushIfDirty() // leaving Generate → flush now
})

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function loadDoc(doc: any) {
  const { nodes: ns, viewport: vp } = workToCanvas(doc)
  setNodes(ns as unknown as FlowNode[]) // persisted nodes are plain data; Vue Flow hydrates the runtime fields
  shownSrc.value = {} // drop the previous work's entries, then seed this work's images
  for (const n of ns) if (n.type === 'image') seedSrc(n.id)
  syncLibraryHidden() // a work saved with a collapsed widget reopens with its palette hidden
  if (vp) setViewport(vp)
  workId.value = doc.id
  title.value = doc.title || ''
  resetBaseline('saved') // the loaded state is the baseline — not dirty
}

// Right-click context menu for removable nodes (images + prompt blocks): download images, arrange, delete.
const ctx = ref<{ show: boolean; x: number; y: number; images: string[]; ids: string[] }>({ show: false, x: 0, y: 0, images: [], ids: [] })
const REMOVABLE = new Set(['image', 'block'])
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function openCtx(event: MouseEvent, targets: any[]) {
  const nodesIn = targets.filter((t) => REMOVABLE.has(t.type))
  if (!nodesIn.length) { ctx.value.show = false; return }
  const images = nodesIn.filter((t) => t.type === 'image').map((t) => t.data?.url).filter(Boolean)
  ctx.value = { show: true, x: event.clientX, y: event.clientY, images, ids: nodesIn.map((t) => t.id) }
}
function closeCtx() { ctx.value.show = false }
function arrangeSelected() {
  const list = ctx.value.ids.map((id) => findNode(id)).filter((n) => n && n.type === 'image')
  closeCtx()
  arrangeImages(list)
}
// Delete the menu's targets; anchor zones/the station are never removable.
function deleteSelected() {
  const ids = ctx.value.ids.filter((id) => !ANCHORS.has(id))
  closeCtx()
  if (ids.length) removeNodes(ids)
}
// Vault-stored images serve as URLs (not data: URIs); fetch and re-encode so /api/download gets raw base64.
async function toBase64(url: string): Promise<string> {
  if (url.startsWith('data:')) return url.slice(url.indexOf(',') + 1)
  const bytes = new Uint8Array(await (await fetch(url)).arrayBuffer())
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(bin)
}
async function downloadImages() {
  const urls = ctx.value.images
  const n = urls.length
  closeCtx()
  try {
    await saveDownloads(await Promise.all(urls.map(toBase64)))
    toast.push(`Saved ${n} image${n > 1 ? 's' : ''} to Downloads`, 'ok')
  } catch (e) {
    toast.push(e instanceof Error ? e.message : 'Download failed', 'err')
  }
}
onNodeContextMenu(({ event, node }) => {
  event.preventDefault()
  if (!REMOVABLE.has(node.type)) { closeCtx(); return }
  const selected = nodes.value.filter((n) => REMOVABLE.has(n.type) && n.selected)
  openCtx(event as MouseEvent, node.selected && selected.length > 1 ? selected : [node])
})
onSelectionContextMenu(({ event, nodes: sel }) => {
  event.preventDefault()
  openCtx(event as MouseEvent, sel.filter((n) => REMOVABLE.has(n.type)))
})
onPaneContextMenu(() => closeCtx())

// Drag an internal splitter to adjust the Output↔composition (v) or Positive↔Negative (h) ratio.
function startSplit(kind: 'v' | 'h', e: MouseEvent) {
  const st = findNode(STATION)
  if (!st) return
  const start = kind === 'v' ? e.clientX : e.clientY
  const startRatio = kind === 'v' ? (st.data.outputRatio ?? 0.3) : (st.data.posRatio ?? 0.5)
  const sd = dims(st) // style fallback → safe if the station isn't currently measured
  const span = kind === 'v' ? sd.w : (sd.h - HEADER - META)
  const onMove = (ev: MouseEvent) => {
    const zoom = viewport.value?.zoom ?? 1
    const delta = ((kind === 'v' ? ev.clientX : ev.clientY) - start) / zoom
    const r = Math.min(0.75, Math.max(0.15, startRatio + delta / span))
    if (kind === 'v') st.data.outputRatio = r
    else st.data.posRatio = r
    relayoutBlocks()
  }
  const onUp = () => {
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
    activeSplitCleanup = null
  }
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
  activeSplitCleanup = onUp // so an unmount mid-drag can still tear these window listeners down
}
let activeSplitCleanup: (() => void) | null = null

// Auto-grow a block to fit its text on expand; restore the collapsed size on collapse.
function toggleExpand(id: string) {
  const live = findNode(id)
  if (!live) return
  const d = live.data
  if (!d.expanded) {
    const style = live.style as { width?: string; height?: string } | undefined
    d._cw = style?.width
    d._ch = style?.height
    const len = String(d.text || '').length
    const height = Math.min(340, Math.max(132, 66 + Math.ceil((len + 1) / 24) * 18))
    live.style = { width: '246px', height: `${height}px` }
    d.expanded = true
  } else {
    live.style = d._ch ? { width: d._cw || '176px', height: d._ch } : { width: d._cw || '176px' }
    d.expanded = false
  }
  if (live.parentNode === LIBRARY) repackLibrary() // the row's height changed — keep the column tight
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function startName(data: any, e: MouseEvent) {
  data.editing = true
  requestAnimationFrame(() => {
    const input = (e.target as HTMLElement).closest('.block')?.querySelector('.bname-input') as HTMLInputElement | null
    input?.focus()
    input?.select()
  })
}
</script>

<template>
  <section class="canvas">
    <div class="projbar">
      <input class="projname" v-model="title" placeholder="Untitled project" />
      <div class="spacer"></div>
      <span v-if="error" class="chip err">{{ error }}</span>
      <button class="barbtn" @click="newWork"><span>＋</span> New work</button>
      <button class="savebtn" :class="saveState" :disabled="saveState === 'saving'" @click="manualSave"
        :title="saveState === 'saved' ? 'Up to date — click or ⌘/Ctrl-S to save now' : 'Save (⌘/Ctrl-S)'">
        <span v-if="saveState === 'saving'" class="spinner sm"></span>
        <span v-else class="ic">{{ saveState === 'saved' ? '✓' : '⤓' }}</span>
        {{ saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? `Saved${savedAt ? ' · ' + savedAt : ''}` : 'Save' }}
      </button>
    </div>

    <div class="flowwrap" ref="flowRef" @drop="onCanvasDrop" @dragover.prevent>
      <div v-if="scratchCount" class="scratch-banner">{{ scratchCount }} scratch item{{ scratchCount > 1 ? 's' : '' }} outside the zones</div>
      <VueFlow :min-zoom="0.2" :max-zoom="2.5" :delete-key-code="null" :only-render-visible-elements="true"
               :multi-selection-key-code="['Control', 'Meta']" :selection-key-code="'Shift'"
               :zoom-on-double-click="false" style="height:100%;width:100%">
        <Background pattern-color="var(--border-strong)" :gap="18" :size="1.2" />
        <Controls position="bottom-left" :show-interactive="false" />

        <template #node-station="{ data, selected }">
          <NodeResizer :min-width="640" :min-height="360" :is-visible="selected" color="var(--accent)" @resize="relayoutBlocks()" />
          <div class="station">
            <div class="sthd">
              <span class="sttitle">Generation</span>
              <span v-if="busy" class="chip busy"><span class="spinner"></span> Generating…</span>
              <div class="spacer"></div>
              <button v-if="busy" class="gzcancel nodrag" @pointerdown.stop @click.stop="emit('cancel')">Cancel</button>
              <button v-else class="gzgen nodrag" :disabled="!composed.positive" @pointerdown.stop @click.stop="doGenerate">Generate</button>
            </div>
            <div class="stbody">
              <div class="stoutput" :style="{ flexGrow: data.outputRatio ?? 0.3 }">
                <div class="colhd">Output</div>
                <div class="outbody">
                  <img v-if="busy && preview" class="liveprev" :src="preview" alt="generating preview" />
                  <div v-else-if="drafts.length" class="topwrap nodrag" :class="{ selected: topSelected }" draggable="true"
                    @dragstart="onDraftDragStart" @pointerdown.stop @click.stop="topSelected = !topSelected" title="Drag onto the canvas to keep">
                    <img class="topimg" :src="drafts[0].url" alt="latest generation" draggable="false" />
                    <span v-if="drafts[0].mock" class="mockbadge" title="Offline placeholder — no NovelAI token set">MOCK</span>
                    <span class="stackbadge">{{ drafts.length }} in stack</span>
                    <span class="draghint">⤴ drag to keep</span>
                  </div>
                  <span v-else-if="!busy" class="outhint">Generated images appear here — drag them out to keep.</span>
                </div>
              </div>
              <div class="vsplit nodrag" title="Drag to resize" @mousedown.stop.prevent="startSplit('v', $event)"></div>
              <div class="stcomp" :style="{ flexGrow: 1 - (data.outputRatio ?? 0.3) }">
                <div class="lane pos" :style="{ flexGrow: data.posRatio ?? 0.5 }"><span class="lanelbl">Positive</span></div>
                <div class="hsplit nodrag" title="Drag to resize" @mousedown.stop.prevent="startSplit('h', $event)"></div>
                <div class="lane neg" :style="{ flexGrow: 1 - (data.posRatio ?? 0.5) }"><span class="lanelbl">Negative</span></div>
              </div>
            </div>
            <div class="stmeta nowheel">
              <div class="mrow"><b>+</b> {{ composed.positive || '—' }}</div>
              <div class="mrow neg"><b>−</b> {{ composed.negative || '—' }}</div>
              <div class="mtok">≈ {{ tokenEstimate }} tokens</div>
            </div>
          </div>
        </template>

        <template #node-image="{ id, data, selected }">
          <div class="imgnode" :class="{ selected, isnew: data.isNew }">
            <span v-if="data.isNew" class="new">NEW</span>
            <span v-if="selected" class="scalebadge">{{ imgScale(id) }}</span>
            <div v-if="data.url" class="imgfull" :style="fullStyle(id, data)">
              <img :src="shownSrc[id] || imgSrc(id, data)" alt="generation" draggable="false" />
            </div>
          </div>
        </template>

        <template #node-zone="{ id, data, selected }">
          <template v-if="data.role === 'library'">
            <NodeResizer v-if="!data.collapsed" :min-width="220" :min-height="180" :is-visible="selected"
              color="var(--accent)" @resize="repackLibrary()" />
            <PromptWidget :data="data" :selected="selected" :count="childCount(id)"
              @toggle="toggleLibraryCollapse" @open-library="emit('navigate', 'library')" />
          </template>
          <template v-else>
            <NodeResizer :min-width="200" :min-height="180" :is-visible="selected" color="var(--accent)" />
            <div class="zonenode" :class="[data.role, { selected }]">
              <div class="zonehd">
                <span class="zicon">▤</span>
                <span class="ztitle">Gallery</span>
                <span class="anchor-tag">anchor</span>
              </div>
              <div v-if="!childCount(id)" class="zhint">Drag kept images here to save them.</div>
            </div>
          </template>
        </template>

        <template #node-block="{ id, data, selected }">
          <NodeResizer :min-width="150" :min-height="34" :is-visible="selected" color="var(--accent)" />
          <div class="block" :class="{ neg: data.polarity === 'negative', expanded: data.expanded }" :style="{ '--cat': catColor(data.category) }">
            <div class="bhd">
              <span class="cdot"></span>
              <input v-if="data.editing" class="bname bname-input nodrag" v-model="data.name"
                @blur="data.editing = false" @keyup.enter="data.editing = false" @keyup.esc="data.editing = false" />
              <span v-else class="bname bname-text" title="double-click to rename" @dblclick.stop="startName(data, $event)">{{ data.name }}</span>
              <button class="bicon nodrag" :title="data.polarity === 'negative' ? 'negative' : 'positive'"
                @click="data.polarity = data.polarity === 'negative' ? 'positive' : 'negative'">{{ data.polarity === 'negative' ? '−' : '＋' }}</button>
              <button class="bicon nodrag" @click="toggleExpand(id)">{{ data.expanded ? '▾' : '▸' }}</button>
            </div>
            <textarea v-if="data.expanded" class="btext nodrag nowheel" v-model="data.text" placeholder="tags…"></textarea>
            <div v-else class="bprev">{{ data.text || 'empty' }}</div>
          </div>
        </template>
      </VueFlow>

      <!-- Scale picker under an all-images selection: click a reference scale to resize the whole set. -->
      <div v-if="scalePicker" class="scalepick" :style="{ left: scalePicker.x + 'px', top: scalePicker.y + 'px' }">
        <button v-for="s in PICK_SCALES" :key="s" :class="{ on: s === scalePicker.current }" @click="applyScaleAll(s)">×{{ s }}</button>
      </div>
    </div>

    <template v-if="ctx.show">
      <div class="ctxback" @click="closeCtx" @contextmenu.prevent="closeCtx"></div>
      <div class="ctxmenu" :style="{ left: ctx.x + 'px', top: ctx.y + 'px' }">
        <button v-if="ctx.images.length > 1" @click="arrangeSelected"><span>▦</span> Arrange evenly</button>
        <button v-if="ctx.images.length" @click="downloadImages"><span>⤓</span> Download {{ ctx.images.length > 1 ? `${ctx.images.length} images` : 'image' }}</button>
        <button class="danger" @click="deleteSelected"><span>🗑</span> Delete {{ ctx.ids.length > 1 ? `${ctx.ids.length} items` : 'item' }}</button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.canvas{display:flex;flex-direction:column;min-width:0;background:var(--bg)}
.projbar{display:flex;align-items:center;gap:10px;padding:11px 20px;border-bottom:1px solid var(--border);flex-shrink:0}
.projname{font-size:16px;font-weight:600;color:var(--text);background:transparent;border:1px solid transparent;border-radius:var(--radius);padding:5px 8px;width:min(300px,40%)}
.projname:hover{border-color:var(--border)}.projname:focus{border-color:var(--accent);outline:none;background:var(--surface-2)}
.projname::placeholder{color:var(--text-faint);font-weight:500}
.projmeta{font-size:12px;color:var(--text-faint)}.dot{opacity:.5}.spacer{flex:1}
.chip{display:inline-flex;align-items:center;gap:7px;font-size:12px;font-weight:500;padding:4px 9px;border-radius:20px;background:var(--surface-2);border:1px solid var(--border);color:var(--text-dim)}
.chip.err{color:#e2483d;border-color:color-mix(in srgb,#e2483d 40%,var(--border))}
.chip.busy{border:0;background:transparent;padding:0}
.chip.warn{color:#e8913a;border-color:color-mix(in srgb,#b65c02 45%,var(--border));background:color-mix(in srgb,#b65c02 14%,var(--surface-1))}
/* Save button that also reflects state (idle/dirty → action, saving, saved → subtle confirmation) */
.savebtn{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);padding:6px 12px;font-size:12px;font-weight:600;cursor:pointer}
.savebtn .ic{font-size:12px;line-height:1}
.savebtn:hover{color:var(--text);border-color:var(--border-strong)}
.savebtn.dirty{background:var(--accent);color:var(--on-accent);border-color:transparent}
.savebtn.dirty:hover{color:var(--on-accent);opacity:.92}
.savebtn.saved,.savebtn.saved .ic{color:#3aa675}
.savebtn.saved:hover{border-color:color-mix(in srgb,#3aa675 45%,var(--border))}
.savebtn.saving{color:var(--text-dim);cursor:default}
.spinner{width:12px;height:12px;border-radius:50%;border:2px solid var(--border-strong);border-top-color:var(--accent);animation:spin .8s linear infinite}
.spinner.sm{width:11px;height:11px;border-width:2px}
@keyframes spin{to{transform:rotate(360deg)}}
.barbtn{border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);padding:6px 10px;font-size:12px;font-weight:500;cursor:pointer;display:flex;align-items:center;gap:6px}
.barbtn:hover{color:var(--text);border-color:var(--border-strong)}
.flowwrap{flex:1;position:relative;min-height:0}
.scratch-banner{position:absolute;top:12px;left:50%;transform:translateX(-50%);z-index:5;pointer-events:none;
  display:flex;align-items:center;font-size:12px;font-weight:500;color:var(--text-dim);padding:5px 13px;border-radius:20px;
  background:color-mix(in srgb,var(--surface-1) 88%,transparent);border:1px solid var(--border);
  box-shadow:0 4px 16px rgba(0,0,0,.25)}
.ctxback{position:fixed;inset:0;z-index:998}
.ctxmenu{position:fixed;z-index:999;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);box-shadow:0 6px 24px rgba(0,0,0,.4);padding:4px;min-width:170px}
.ctxmenu button{display:flex;align-items:center;gap:8px;width:100%;border:0;background:transparent;color:var(--text);font-size:13px;padding:8px 10px;border-radius:var(--radius);cursor:pointer;text-align:left}
.ctxmenu button:hover{background:var(--surface-3)}
.ctxmenu button.danger{color:var(--danger,#e2483d)}
.ctxmenu button.danger:hover{background:color-mix(in srgb,var(--danger,#e2483d) 15%,transparent)}
.flowwrap :deep(.vue-flow__node){cursor:grab;border-radius:8px}
.flowwrap :deep(.vue-flow__controls){box-shadow:0 2px 10px rgba(0,0,0,.3);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.flowwrap :deep(.vue-flow__controls-button){background:var(--surface-2);border-bottom:1px solid var(--border);width:26px;height:26px;padding:6px}
.flowwrap :deep(.vue-flow__controls-button svg){fill:var(--text-dim)}
.flowwrap :deep(.vue-flow__controls-button:hover){background:var(--surface-3)}
.flowwrap :deep(.vue-flow__controls-button:hover svg){fill:var(--text)}
.flowwrap :deep(.vue-flow__node.selected){box-shadow:0 0 0 2px var(--accent)}
.flowwrap :deep(.vue-flow__selection){background:color-mix(in srgb,var(--accent) 14%,transparent);border:1px solid var(--accent);border-radius:4px}
.flowwrap :deep(.vue-flow__nodesselection-rect){background:color-mix(in srgb,var(--accent) 10%,transparent);border:1px solid var(--accent);border-radius:4px}

/* station (coupled Output + composition) */
.station{width:100%;height:100%;display:flex;flex-direction:column;border:1.5px solid var(--border-strong);border-radius:12px;overflow:hidden;background:color-mix(in srgb,var(--surface-1) 70%,transparent)}
.sthd{height:44px;flex-shrink:0;display:flex;align-items:center;gap:8px;padding:0 12px;border-bottom:1px solid var(--border);background:var(--surface-1)}
.sttitle{font-weight:600;font-size:13px}
.gzgen{border:0;border-radius:var(--radius);background:var(--accent);color:var(--on-accent);font-weight:600;font-size:12px;padding:6px 14px;cursor:pointer}
.gzgen:disabled{opacity:.5;cursor:default}
.gzcancel{border:1px solid var(--border-strong);border-radius:var(--radius);background:transparent;color:var(--text);font-weight:600;font-size:12px;padding:6px 14px;cursor:pointer}
.gzcancel:hover{border-color:var(--danger,#e2483d);color:var(--danger,#e2483d)}
.stbody{flex:1;display:flex;min-height:0}
.stoutput{flex-basis:0;min-width:120px;display:flex;flex-direction:column;background:color-mix(in srgb,var(--surface-2) 40%,transparent)}
.colhd{height:26px;flex-shrink:0;display:flex;align-items:center;padding:0 12px;font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint);border-bottom:1px solid var(--border)}
.outbody{flex:1;position:relative}
.vsplit{width:6px;flex-shrink:0;cursor:col-resize;background:var(--border)}
.vsplit:hover{background:var(--accent)}
.hsplit{height:6px;flex-shrink:0;cursor:row-resize;background:var(--border)}
.hsplit:hover{background:var(--accent)}
.outhint{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;text-align:center;padding:20px;font-size:12px;color:var(--text-faint)}
.liveprev{position:absolute;inset:8px;width:calc(100% - 16px);height:calc(100% - 16px);object-fit:contain;border-radius:8px;
  border:1px solid var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 30%,transparent)}
.topwrap{position:absolute;inset:8px;display:flex;align-items:center;justify-content:center;cursor:grab}
.topwrap:active{cursor:grabbing}
.topimg{max-width:100%;max-height:100%;object-fit:contain;border-radius:8px;border:1px solid var(--border);box-shadow:0 2px 10px rgba(0,0,0,.35);transition:box-shadow .12s,border-color .12s}
.topwrap:hover .topimg{border-color:var(--border-strong)}
.mockbadge{position:absolute;top:14px;left:14px;font-size:9px;font-weight:800;letter-spacing:.5px;background:var(--warn,#b65c02);color:#fff;padding:2px 7px;border-radius:10px;pointer-events:none}
.topwrap.selected .topimg{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 45%,transparent)}
.stackbadge{position:absolute;top:2px;right:2px;font-size:10px;font-weight:600;background:color-mix(in srgb,#000 58%,transparent);color:#fff;padding:2px 8px;border-radius:20px}
.draghint{position:absolute;bottom:8px;left:50%;transform:translateX(-50%);font-size:10px;font-weight:600;background:var(--accent);color:var(--on-accent);padding:3px 10px;border-radius:20px;opacity:0;transition:opacity .12s;pointer-events:none;white-space:nowrap}
.topwrap:hover .draghint{opacity:1}
.stcomp{flex-basis:0;min-width:0;display:flex;flex-direction:column}
.lane{flex-basis:0;min-height:0;position:relative}
.lane.pos{background:color-mix(in srgb,var(--accent) 6%,transparent)}
.lane.neg{background:color-mix(in srgb,#e2483d 6%,transparent)}
.lanelbl{position:absolute;left:10px;top:8px;font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint)}
.stmeta{height:66px;flex-shrink:0;border-top:1px solid var(--border);background:var(--surface-1);padding:7px 12px;overflow:auto;font-size:11px;color:var(--text-dim)}
.stmeta .mrow{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stmeta .mrow b{color:var(--text-faint)}.stmeta .mrow.neg b{color:#e2483d}
.stmeta .mtok{margin-top:2px;color:var(--text-faint)}

/* The min size must stay below any legit node box (×0.5 portrait ≈ 62×90) — a larger clamp makes the
   card outgrow the node while the image inside stays transform-scaled to the node box (right/bottom gap). */
.imgnode{position:relative;width:100%;height:100%;min-width:40px;min-height:40px;border-radius:8px;overflow:hidden;border:1px solid var(--border);background:var(--surface-2);box-shadow:0 2px 6px rgba(0,0,0,.25)}
.imgnode.selected,.imgnode.isnew{border-color:var(--accent)}
.imgnode.selected{box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 45%,transparent)}
/* Full-resolution decode, CSS-downscaled to the node. The browser decodes an <img> at its *layout* size;
   letting the node shrink the img (to ~90px at ×0.5) makes it decode tiny, then scaling back up smears
   that cached low-res decode. So we lay the img out at a fixed high resolution (`.imgfull`) and shrink it
   with a transform — decode stays full-res and the GPU only downscales, which is crisp (Figma/Miro do this). */
.imgnode .imgfull{position:absolute;top:0;left:0}
.imgnode .imgfull img{display:block;width:100%;height:100%;object-fit:cover}
.imgnode .new{position:absolute;left:5px;top:5px;font-size:9px;font-weight:700;background:var(--accent);color:#fff;padding:1px 6px;border-radius:10px;z-index:1}
.imgnode .scalebadge{position:absolute;right:5px;top:5px;font-size:10px;font-weight:700;background:color-mix(in srgb,#000 55%,transparent);color:#fff;padding:1px 7px;border-radius:10px;z-index:1}
/* clickable scale picker under an all-images selection */
.scalepick{position:absolute;z-index:20;transform:translateX(-50%);display:flex;gap:2px;padding:3px;
  background:color-mix(in srgb,var(--surface-1) 82%,transparent);border:1px solid var(--border-strong);
  border-radius:20px;box-shadow:0 4px 16px rgba(0,0,0,.45);backdrop-filter:blur(6px)}
.scalepick button{border:0;background:transparent;color:var(--text-dim);font-size:11px;font-weight:600;
  padding:4px 9px;border-radius:16px;cursor:pointer;white-space:nowrap}
.scalepick button:hover{background:var(--surface-3);color:var(--text)}
.scalepick button.on{background:var(--accent);color:var(--on-accent)}

.zonenode{width:100%;height:100%;border:1.5px solid var(--border-strong);border-radius:12px;overflow:hidden;background:color-mix(in srgb,var(--surface-1) 60%,transparent)}
.zonenode.selected{border-color:var(--accent)}
.zonehd{display:flex;align-items:center;gap:8px;height:38px;padding:0 12px;border-bottom:1px solid var(--border);background:var(--surface-1);font-weight:600;font-size:13px}
.zonehd .zicon{font-style:normal}
.zonehd .anchor-tag{margin-left:auto;font-size:10px;font-weight:600;color:var(--accent);background:color-mix(in srgb,var(--accent) 16%,transparent);padding:1px 7px;border-radius:20px}
.zhint{padding:16px;font-size:12px;color:var(--text-faint);text-align:center}

.block{width:100%;height:100%;min-height:34px;display:flex;flex-direction:column;border-radius:8px;border:1px solid var(--border);border-left:3px solid var(--cat);background:var(--surface-2);box-shadow:0 1px 4px rgba(0,0,0,.2);overflow:hidden}
.block.neg{border-left-color:#e2483d}
.bhd{flex-shrink:0;display:flex;align-items:center;gap:6px;padding:6px 8px}
.cdot{width:8px;height:8px;border-radius:50%;background:var(--cat);flex-shrink:0}
.block.neg .cdot{background:#e2483d}
.bname{flex:1;min-width:0;font:inherit;font-size:12px;font-weight:600;color:var(--text);background:transparent;border:1px solid transparent;border-radius:4px;padding:2px 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bname:focus{outline:none;border-color:var(--accent);background:var(--surface-1)}
.bname-text{cursor:default}
.bicon{width:20px;height:20px;flex-shrink:0;border:1px solid var(--border);background:var(--surface-1);color:var(--text-dim);border-radius:5px;font-size:11px;cursor:pointer;line-height:1}
.bicon:hover{color:var(--text);border-color:var(--border-strong)}
.bprev{padding:0 10px 8px;font-size:11px;color:var(--text-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.btext{flex:1;margin:0 8px 8px;width:calc(100% - 16px);min-height:52px;resize:none;font:inherit;font-size:11px;color:var(--text);background:var(--surface-1);border:1px solid var(--border);border-radius:5px;padding:6px;outline:none}
.btext:focus{border-color:var(--accent)}
</style>
