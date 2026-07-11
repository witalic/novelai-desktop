<script setup lang="ts">
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { VueFlow, useVueFlow, type Node as FlowNode } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { NodeResizer } from '@vue-flow/node-resizer'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/node-resizer/dist/style.css'
import { getVaultConfig, listCategories, saveDownloads } from '../api'
import { useToast } from '../composables/useToast'
import { useConfirm } from '../composables/useConfirm'
import { useImagePipeline, PICK_SCALES, BASE_LONG, DEFAULT_SPAWN_SCALE } from '../composables/useImagePipeline'
import { useAccount } from '../composables/useAccount'
import { useCatalog, modelSpec } from '../composables/useCatalog'
import { useTokenCount } from '../composables/useTokenCount'
import { useContextMenu, type MenuItem } from '../composables/useContextMenu'
import { useImagePreview } from '../composables/useImagePreview'
import { anlasCost } from '../presets/cost'
import { useAutosave } from '../composables/useAutosave'
import { workToCanvas, GALLERY, LIBRARY, STATION } from '../vault/serialize'
import { appendX } from '../canvas/pack'
import { reorderIds } from '../canvas/palette'
import { dedupePrompt } from '../canvas/dedup'
import PromptWidget from './PromptWidget.vue'
import { newId } from '../vault/ids'
import { onBeforeQuit } from '../electron'
import type { GenResult, LibraryBlock, PanelParams, PersistedComponent, SnapshotData, WorkDoc } from '../types'

const toast = useToast()
const { confirm } = useConfirm()
const props = defineProps<{
  drafts: GenResult[]; busy: boolean; error: string; preview: string; params: PanelParams
  openWork: WorkDoc | null; insertBlocks?: { blocks: LibraryBlock[]; nonce: number } | null
  linkPin?: { nodeId: string; block: LibraryBlock; nonce: number } | null
  keepDrafts?: { ids: string[]; nonce: number } | null
}>()
const emit = defineEmits<{
  generate: [{ positive: string; negative: string; snapshot: SnapshotData }]
  take: [string]
  cancel: []
  saved: [string]
  navigate: [string]
  'save-block': [{ nodeId: string; block: LibraryBlock }]
  'new-work': []
}>()

const {
  nodes, addNodes, removeNodes, findNode, onNodeDragStop, getIntersectingNodes, viewport,
  screenToFlowCoordinate, setNodes, setViewport, onNodeContextMenu, onSelectionContextMenu, onPaneContextMenu,
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
// Bumped whenever the vault's Library may have changed under the widget (returning to Generate,
// opening a work) so the prompt widget re-reads category colors/counts and its pins' versions —
// KeepAlive keeps the widget mounted, so it can't rely on its own onMounted firing again.
const widgetRevalidate = ref(0)
// Colors come from the vault's categories (customs have their own); CATS is the offline fallback.
const vaultCatColors = ref<Record<string, string>>({})
async function loadCategoryColors() {
  try {
    vaultCatColors.value = Object.fromEntries((await listCategories()).map((c) => [c.slug, c.color]))
  } catch { /* backend not ready / no vault — fall back to the builtin palette */ }
}
const catColor = (c: string) => vaultCatColors.value[c] ?? CATS[c] ?? CATS.custom

// Estimated Anlas cost of the current params (Opus tier gets the first sample free — see cost.ts).
const { subscription } = useAccount()
const genCost = computed(() => anlasCost(props.params, subscription.value?.tier ?? 0, !!subscription.value?.active))

const compBlocks = computed(() => nodes.value.filter((n) => n.type === 'block' && n.parentNode === STATION))
const composed = computed(() => {
  const pick = (neg: boolean) => compBlocks.value
    .filter((b) => (b.data.polarity === 'negative') === neg)
    .slice().sort((a, b) => a.position.x - b.position.x)
    .map((b) => String(b.data.text || '').trim()).filter(Boolean)
  let positive = pick(false).join(', ')
  let negative = pick(true).join(', ')
  // "Unique tags" — merge repeated tags (summing weights) before this prompt drives the indicator,
  // the meta preview, and generation, so all three agree on exactly what will be sent.
  if (props.params.dedupe) { positive = dedupePrompt(positive); negative = dedupePrompt(negative) }
  return { positive, negative }
})
// Real token usage vs the model's budget (POST /api/tokenize, debounced + stale-guarded). Positive
// (base + character captions later) and negative are counted separately against their own limits.
useCatalog() // ensure the catalog is loaded so the per-model limits below resolve
const { positive: posTokens, negative: negTokens } = useTokenCount(
  () => ({
    model: props.params.model, positive: composed.value.positive, negative: composed.value.negative,
    // NovelAI prepends quality tags to the positive and the ucPreset undesired-content to the negative;
    // both count against their budgets, so the indicator mirrors the web UI.
    quality_toggle: props.params.quality_toggle, uc_preset: props.params.uc_preset,
  }),
)
const posLimit = computed(() => modelSpec(props.params.model)?.token_limit ?? 512)
const negLimit = computed(() => modelSpec(props.params.model)?.negative_token_limit ?? 512)
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

// Size a freshly-kept image at the default spawn scale, keeping its aspect ratio. One place so
// every spawn site (single drag, multi-keep) agrees on the default.
function spawnSize(ar: number) {
  const long = BASE_LONG * DEFAULT_SPAWN_SCALE
  return { w: Math.round(ar >= 1 ? long : long * ar), h: Math.round(ar >= 1 ? long / ar : long) }
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
  const payload = e.dataTransfer?.getData('text/plain') || ''
  if (payload.startsWith('nai-palette:')) { // a palette row dragged out → independent copy at the drop point
    const src = findNode(payload.slice('nai-palette:'.length))
    if (src?.parentNode === LIBRARY) dropBlockAt(cloneBlockData(src.data), toFlow(e.clientX, e.clientY))
    return
  }
  if (payload.startsWith('nai-libblock:')) { // a browse row dragged out → copy of the vault block
    try {
      const b = JSON.parse(payload.slice('nai-libblock:'.length)) as LibraryBlock
      dropBlockAt(libraryBlockData(b), toFlow(e.clientX, e.clientY))
    } catch { /* malformed payload — ignore */ }
    return
  }
  if (payload.startsWith('nai-drafts:')) { // a multi-selected group of drafts dragged out
    keepDraftsBatch(payload.slice('nai-drafts:'.length).split(','), toFlow(e.clientX, e.clientY))
    return
  }
  // Station Output slot drags the top (`nai-draft`); a Stack-tab thumbnail drags a specific draft
  // (`nai-draft:<id>`). Anything else is an external file — ignore.
  let draft: GenResult | undefined
  if (payload === 'nai-draft') draft = props.drafts[0]
  else if (payload.startsWith('nai-draft:')) draft = props.drafts.find((d) => d.id === payload.slice('nai-draft:'.length))
  else return
  if (!draft) return
  const pos = toFlow(e.clientX, e.clientY)
  const ar = (draft.params.width || 832) / (draft.params.height || 1216)
  const { w, h } = spawnSize(ar)
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
  emit('take', draft.id) // remove exactly the kept draft (the top from the slot, or a specific thumb)
}

// Keep several drafts at once (Stack multi-select — button or a multi-drag): materialise each in
// stack order, arrange without overlapping what's already there, then prune each via `take`.
// `pos` (flow coords) is the drop point for a drag; absent = the "Keep on canvas" button → gallery.
function keepDraftsBatch(ids: string[], pos?: { x: number; y: number }) {
  const gal = findNode(GALLERY)
  const gp = gal ? (gal.computedPosition || gal.position) : { x: 0, y: 0 }
  const gd = gal ? dims(gal) : { w: 0, h: 0 }
  const inGallery = !!gal && (!pos || (pos.x >= gp.x && pos.x <= gp.x + gd.w && pos.y >= gp.y && pos.y <= gp.y + gd.h))
  const placed: string[] = []
  ids.forEach((did, i) => {
    const draft = props.drafts.find((d) => d.id === did)
    if (!draft) return
    const ar = (draft.params.width || 832) / (draft.params.height || 1216)
    const { w, h } = spawnSize(ar)
    const data = { url: draft.url, file: draft.file || '', snapshot: draft.snapshot, ar, created_at: draft.created_at || new Date().toISOString() }
    if (inGallery && gal) {
      // Land far below existing gallery images (huge staggered y) so the re-arrange appends them
      // after what's already kept — never on top of it.
      addNodes([{ id: did, type: 'image', parentNode: GALLERY, zIndex: 3, style: { width: `${w}px`, height: `${h}px` }, position: { x: 12, y: 1e6 + i }, data }])
    } else {
      const base = pos ?? { x: 60, y: 60 }
      addNodes([{ id: did, type: 'image', zIndex: 3, style: { width: `${w}px`, height: `${h}px` }, position: { x: base.x - w / 2 + i, y: base.y - h / 2 + i }, data }])
    }
    seedSrc(did)
    placed.push(did)
  })
  if (!placed.length) return
  nextTick(() => {
    if (inGallery && gal) {
      arrangeImages(nodes.value.filter((n) => n.type === 'image' && n.parentNode === GALLERY)) // pack the whole gallery
    } else {
      arrangeImages(placed.map((id) => findNode(id)).filter(Boolean), pos ? new Map([['', pos]]) : undefined)
    }
    placed.forEach((id) => emit('take', id))
  })
}
watch(() => props.keepDrafts?.nonce, () => { if (props.keepDrafts?.ids.length) keepDraftsBatch(props.keepDrafts.ids) })

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
    { id: LIBRARY, type: 'zone', position: { x: 40, y: 40 }, data: { role: 'library' }, zIndex: 0, style: { width: '260px', height: '460px' } },
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
  emit('new-work') // GenerateView re-seeds params from the default preset
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
      // Not in the composition — drop onto the widget pins it (appended; the widget list is the
      // palette's home, canvas blocks never live inside the zone visually), or detach to scratch.
      const lib = getIntersectingNodes(node).find((n) => n.id === LIBRARY)
      if (lib) {
        live.parentNode = LIBRARY
        live.position = { x: 0, y: nextPaletteY() }
        live.hidden = true
        live.data = { ...live.data, expanded: false, editing: false }
      } else if (live.parentNode) {
        live.position = { x: node.computedPosition.x, y: node.computedPosition.y }
        live.parentNode = undefined
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

// ---- prompt widget (library zone) ----
// The palette's truth is the zone's child block nodes (persisted as before; position.y is the
// order key), but they NEVER render on the canvas — the widget shows them as a scrollable HTML
// list (design/prompt-widget-mockup.html). No packing geometry, native scroll, dozens of pins.
const paletteChildren = computed(() => nodes.value.filter((n) => n.type === 'block' && n.parentNode === LIBRARY))
const paletteRows = computed(() => paletteChildren.value
  .slice().sort((a, b) => a.position.y - b.position.y)
  .map((n) => ({
    nodeId: n.id,
    name: (n.data?.name as string) || '',
    text: (n.data?.text as string) || '',
    polarity: (((n.data?.polarity as string) === 'negative') ? 'negative' : 'positive') as 'positive' | 'negative',
    category: (n.data?.category as string) || 'custom',
    tags: (n.data?.tags as string[]) || [],
    block_id: n.data?.block_id as string | undefined,
    version: n.data?.version as number | undefined,
  })))
const nextPaletteY = () => (paletteChildren.value.length
  ? Math.max(...paletteChildren.value.map((n) => n.position.y)) + 10 : 0)
// A freshly authored custom block lands at the TOP of the palette (y below the current minimum).
const prevPaletteY = () => (paletteChildren.value.length
  ? Math.min(...paletteChildren.value.map((n) => n.position.y)) - 10 : 0)
// Palette nodes stay hidden permanently — set on every path that parents a block to the zone.
function hidePaletteNodes() {
  for (const n of nodes.value) if (n.parentNode === LIBRARY) n.hidden = true
}

// The widget edits palette content through this single mutation point (deep watcher → autosave).
function patchPaletteNode(p: { nodeId: string; patch: Record<string, unknown> }) {
  const n = findNode(p.nodeId)
  if (n && n.parentNode === LIBRARY) n.data = { ...n.data, ...p.patch }
}

// Reorder via the pure helper, then renumber y as 0,10,20… (y is the palette order key).
function reorderPalette(p: { nodeId: string; beforeId: string | null }) {
  const ordered = paletteChildren.value.slice().sort((a, b) => a.position.y - b.position.y)
  reorderIds(ordered.map((n) => n.id), p.nodeId, p.beforeId).forEach((id, i) => {
    const n = findNode(id)
    if (n) n.position = { x: 0, y: i * 10 }
  })
}

// Collapse persists with the work (layout layer). Height must be written to BOTH the node's
// numeric size (what NodeResizer mutates — a stale style.height would otherwise be ignored) and
// its style, or the expanded height won't restore.
function setZoneHeight(zone: any, h: number) {
  zone.style = { ...(zone.style as object), height: `${h}px` }
  zone.height = h
  if (zone.dimensions) zone.dimensions = { ...zone.dimensions, height: h }
}
function toggleLibraryCollapse() {
  const zone = findNode(LIBRARY)
  if (!zone) return
  if (!zone.data.collapsed) {
    zone.data.expandedH = Math.max(260, Math.round(dims(zone).h)) // remember the live height
    zone.data.collapsed = true
    setZoneHeight(zone, 38)
  } else {
    zone.data.collapsed = false
    setZoneHeight(zone, zone.data.expandedH || 460)
  }
}

// ---- palette copy semantics: the pin is a palette master; every exit is an independent copy ----
// block_ids pinned in the palette — the widget's browse pool excludes them (exclusive membership).
const pinnedIds = computed(() => paletteChildren.value
  .filter((n) => n.data?.block_id).map((n) => n.data.block_id as string))

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function cloneBlockData(d: any) {
  // Domain fields only — a copy is frozen content + the vault ref, never transient UI state.
  return { category: d.category, name: d.name, text: d.text, polarity: d.polarity,
    block_id: d.block_id, version: d.version, tags: [...(d.tags || [])], expanded: false }
}

// A block already contributes to the prompt if the composition holds one with the same polarity +
// text (its identity for generation). Copying an identical one adds nothing, so we warn instead of
// duplicating — regardless of the copy method (＋, ⇢, or drag into the station).
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const blockKey = (polarity: any, text: any) => `${polarity === 'negative' ? 'neg' : 'pos'}:${String(text || '').trim().toLowerCase()}`
function stationHasBlock(polarity: string, text: string): boolean {
  const key = blockKey(polarity, text)
  return nodes.value.some((n) => n.type === 'block' && n.parentNode === STATION && blockKey(n.data?.polarity, n.data?.text) === key)
}
function warnDuplicate() {
  toast.push('That block is already in the generation area — not duplicated', 'err')
}

// ✕ on a palette row: the block leaves the palette (its block_id returns to the browse pool).
function unpinBlock(id: string) {
  removeNodes([id])
}

// Drag-out of a widget row: an independent copy lands where it was dropped — inside the station
// composition (polarity re-derived from the drop y) or loose on the canvas as scratch.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function dropBlockAt(data: any, pos: { x: number; y: number }) {
  const st = findNode(STATION)
  const stPos = st ? (st.computedPosition || st.position) : { x: 0, y: 0 }
  const rel = { x: pos.x - stPos.x, y: pos.y - stPos.y }
  const { w: stW, h } = st ? dims(st) : { w: 0, h: 0 }
  const outputW = (st?.data.outputRatio ?? 0.3) * stW
  const laneBoundary = HEADER + (st?.data.posRatio ?? 0.5) * (h - HEADER - META)
  const bw = 176, bh = 34
  const inComposition = !!st && rel.x >= outputW && rel.x <= stW && rel.y >= HEADER && rel.y <= h - META
  if (inComposition) {
    const polarity = rel.y < laneBoundary ? 'positive' : 'negative'
    if (stationHasBlock(polarity, data.text)) { warnDuplicate(); return }
    const lTop = polarity === 'negative' ? laneBoundary : HEADER
    const lBot = polarity === 'negative' ? (h - META) : laneBoundary
    addNodes([{
      id: newId('blk'), type: 'block', parentNode: STATION, zIndex: 2,
      position: { x: rel.x - bw / 2, y: rel.y - bh / 2 }, style: { width: `${bw}px` },
      data: {
        ...data, polarity,
        xFrac: clamp01((rel.x - bw / 2 - outputW) / Math.max(1, (stW - outputW) - bw)),
        laneFrac: clamp01((rel.y - bh / 2 - lTop) / Math.max(1, (lBot - lTop) - bh)),
      },
    }])
  } else {
    addNodes([{
      id: newId('blk'), type: 'block', zIndex: 2,
      position: { x: pos.x - bw / 2, y: pos.y - bh / 2 }, style: { width: `${bw}px` },
      data,
    }])
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function libraryBlockData(b: LibraryBlock): any {
  return { category: b.category, name: b.name, text: b.text, polarity: b.polarity,
    block_id: b.id, version: b.version ?? 1, tags: [...(b.tags ?? [])], expanded: false }
}

// ⇢: an independent copy lands at the end of the station lane matching the block's polarity
// (strict routing — drag if you want the other lane). Serves both palette and browse rows.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function appendToStationLane(data: any) {
  const st = findNode(STATION)
  if (!st) return
  if (stationHasBlock(data.polarity, data.text)) { warnDuplicate(); return }
  const { w: stW, h } = dims(st)
  const outputW = (st.data.outputRatio ?? 0.3) * stW
  const laneBoundary = HEADER + (st.data.posRatio ?? 0.5) * (h - HEADER - META)
  const neg = data.polarity === 'negative'
  const laneTop = neg ? laneBoundary : HEADER
  const laneBot = neg ? (h - META) : laneBoundary
  const lane = nodes.value.filter((n) => n.type === 'block' && n.parentNode === STATION
    && (n.data.polarity === 'negative') === neg)
  const bw = 176, bh = 34
  const x = Math.min(appendX(lane.map((b) => ({ x: b.position.x, w: dims(b).w })), outputW), stW - bw - 8)
  const y = Math.min(laneTop + 10, Math.max(laneTop, laneBot - bh - 4))
  addNodes([{
    id: newId('blk'), type: 'block', parentNode: STATION, zIndex: 2,
    position: { x, y }, style: { width: `${bw}px` },
    data: {
      ...data,
      xFrac: clamp01((x - outputW) / Math.max(1, (stW - outputW) - bw)),
      laneFrac: clamp01((y - laneTop) / Math.max(1, (laneBot - laneTop) - bh)),
    },
  }])
}
function copyToStation(paletteId: string) {
  const live = findNode(paletteId)
  if (live?.parentNode === LIBRARY) appendToStationLane(cloneBlockData(live.data))
}
function useLibraryBlock(b: LibraryBlock) {
  appendToStationLane(libraryBlockData(b))
}

// ＋ New custom block: a work-local block (no block_id → 'local' badge) at the top of the palette;
// the widget expands it for editing right away.
function addCustomBlock() {
  addNodes([{
    id: newId('blk'), type: 'block', parentNode: LIBRARY, zIndex: 2, hidden: true,
    position: { x: 0, y: prevPaletteY() },
    data: { category: 'custom', name: 'Untitled', text: '', polarity: 'positive', tags: [] },
  }])
}

// ↥ on a local palette row: author it into the vault. The Library editor drawer opens prefilled
// (App mediates the tab switch); on save the pin links back via the linkPin prop below.
function saveToLibrary(nodeId: string) {
  const live = findNode(nodeId)
  if (!live) return
  if (!String(live.data.text || '').trim()) {
    toast.push('Add prompt text before saving to the Library', 'err')
    return
  }
  emit('save-block', {
    nodeId,
    block: {
      id: newId('block'), category: live.data.category || 'custom', name: live.data.name || '',
      text: live.data.text, polarity: live.data.polarity || 'positive', tags: [...(live.data.tags || [])],
    },
  })
}

// After the drawer saves: link the pin to the vault block and adopt the drawer's (possibly
// refined) content — otherwise the pin would drift from v1 the moment it was born.
watch(() => props.linkPin?.nonce, () => {
  const link = props.linkPin
  if (!link) return
  const n = findNode(link.nodeId)
  if (!n || n.parentNode !== LIBRARY) return
  n.data = {
    ...n.data, block_id: link.block.id, version: link.block.version ?? 1,
    category: link.block.category, name: link.block.name, text: link.block.text,
    polarity: link.block.polarity, tags: [...link.block.tags],
  }
})

// A block "used" from the Library tab (or pinned in browse) lands in the palette, keeping its
// vault link (block_id/version/tags) so generated snapshots and inherited image tags stay traceable.
function insertLibraryBlock(b: LibraryBlock) {
  if (pinnedIds.value.includes(b.id)) return // exclusive membership — never double-pin the same vault block
  addNodes([{
    // Unique node id (not a per-mount counter) so re-inserting a block into a reloaded work can't collide.
    id: newId(`lib-${b.id}`), type: 'block', parentNode: LIBRARY, zIndex: 2, hidden: true,
    position: { x: 0, y: nextPaletteY() }, // y is the palette order key — append at the end
    data: {
      category: b.category, name: b.name, text: b.text, polarity: b.polarity,
      block_id: b.id, version: b.version ?? 1, tags: b.tags ?? [],
    },
  }])
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
  const t = e.target as HTMLElement | null
  const typing = !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
  if (e.key === '/' && !typing) {
    e.preventDefault()
    window.dispatchEvent(new CustomEvent('nai:widget-search')) // the prompt widget opens browse + focuses search
    return
  }
  if (e.key === 'Delete' || e.key === 'Backspace') {
    if (typing) return // typing, not deleting
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
  checkVault()          // a vault may have been configured while we were away
  loadCategoryColors()  // category colors may have changed in the Library tab
  widgetRevalidate.value++ // the widget re-checks pin versions + categories (Library edits land here)
  refreshInterval()     // pick up a changed autosave interval
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
  hidePaletteNodes() // palette blocks render in the widget list, never on the canvas
  widgetRevalidate.value++ // a freshly opened work re-checks its pins' versions
  if (vp) setViewport(vp)
  workId.value = doc.id
  title.value = doc.title || ''
  resetBaseline('saved') // the loaded state is the baseline — not dirty
}

// Right-click menu for images + prompt blocks (shared ContextMenu): preview, arrange, download, delete.
const { open: openMenu, close: closeMenu } = useContextMenu()
const { preview: openPreview } = useImagePreview() // `preview` is already a prop (the live generation image)
const REMOVABLE = new Set(['image', 'block'])
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function openNodeMenu(event: MouseEvent, targets: any[]) {
  const nodesIn = targets.filter((t) => REMOVABLE.has(t.type))
  if (!nodesIn.length) { closeMenu(); return }
  const ids = nodesIn.map((t) => t.id)
  const imgs = nodesIn.filter((t) => t.type === 'image')
  const urls = imgs.map((t) => t.data?.url).filter(Boolean) as string[]
  const items: MenuItem[] = []
  if (urls.length === 1) items.push({ label: 'Preview', icon: '⤢', onClick: () => openPreview(urls[0]) })
  if (imgs.length > 1) items.push({ label: 'Arrange evenly', icon: '▦', onClick: () => arrangeImages(imgs.map((n) => findNode(n.id)).filter(Boolean)) })
  if (urls.length) items.push({ label: `Download${urls.length > 1 ? ` ${urls.length} images` : ' image'}`, icon: '⤓', onClick: () => downloadImages(urls) })
  // Anchor zones / the station are never removable — filter them from the delete set.
  items.push({ label: `Delete${ids.length > 1 ? ` ${ids.length} items` : ' item'}`, icon: '🗑', danger: true,
    onClick: () => { const del = ids.filter((id) => !ANCHORS.has(id)); if (del.length) removeNodes(del) } })
  openMenu(event, items)
}
// Vault-stored images serve as URLs (not data: URIs); fetch and re-encode so /api/download gets raw base64.
async function toBase64(url: string): Promise<string> {
  if (url.startsWith('data:')) return url.slice(url.indexOf(',') + 1)
  const bytes = new Uint8Array(await (await fetch(url)).arrayBuffer())
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(bin)
}
async function downloadImages(urls: string[]) {
  const n = urls.length
  try {
    await saveDownloads(await Promise.all(urls.map(toBase64)))
    toast.push(`Saved ${n} image${n > 1 ? 's' : ''} to Downloads`, 'ok')
  } catch (e) {
    toast.push(e instanceof Error ? e.message : 'Download failed', 'err')
  }
}
// Right-click the station Output image → same actions as a stack thumbnail (top draft).
function onStationMenu(e: MouseEvent) {
  const d = props.drafts[0]
  if (!d) return
  openMenu(e, [
    { label: 'Preview', icon: '⤢', onClick: () => openPreview(d.url) },
    { label: 'Move to canvas', icon: '⤒', onClick: () => keepDraftsBatch([d.id]) },
    { label: 'Remove from stack', icon: '🗑', danger: true, onClick: () => emit('take', d.id) },
  ])
}
onNodeContextMenu(({ event, node }) => {
  event.preventDefault()
  if (!REMOVABLE.has(node.type)) { closeMenu(); return }
  const selected = nodes.value.filter((n) => REMOVABLE.has(n.type) && n.selected)
  openNodeMenu(event as MouseEvent, node.selected && selected.length > 1 ? selected : [node])
})
onSelectionContextMenu(({ event, nodes: sel }) => {
  event.preventDefault()
  openNodeMenu(event as MouseEvent, sel.filter((n) => REMOVABLE.has(n.type)))
})
onPaneContextMenu(() => closeMenu())

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
              <span v-if="subscription" class="stbal nodrag" :title="`${subscription.tier_name} subscription — remaining Anlas`">
                <span class="sttier">{{ subscription.tier_name }}</span>◆ {{ subscription.anlas.toLocaleString() }}
              </span>
              <button v-if="busy" class="gzcancel nodrag" @pointerdown.stop @click.stop="emit('cancel')">Cancel</button>
              <button v-else class="gzgen nodrag" :disabled="!composed.positive" @pointerdown.stop @click.stop="doGenerate">
                Generate <span class="gcost">{{ genCost === 0 ? 'free' : `◆ ${genCost}` }}</span>
              </button>
            </div>
            <div class="stbody">
              <div class="stoutput" :style="{ flexGrow: data.outputRatio ?? 0.3 }">
                <div class="colhd">Output</div>
                <div class="outbody">
                  <img v-if="busy && preview" class="liveprev" :src="preview" alt="generating preview" />
                  <div v-else-if="drafts.length" class="topwrap nodrag" :class="{ selected: topSelected }" draggable="true"
                    @dragstart="onDraftDragStart" @pointerdown.stop @click.stop="topSelected = !topSelected"
                    @contextmenu.stop="onStationMenu" title="Drag onto the canvas to keep · right-click for actions">
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
              <div class="mrow"><b>+</b> <span class="mtext">{{ composed.positive || '—' }}</span>
                <span class="tok" :class="{ over: posTokens > posLimit }" :title="`Positive prompt — ${posTokens} of ${posLimit} T5 tokens`">{{ posTokens }} / {{ posLimit }} tokens</span></div>
              <div class="mrow neg"><b>−</b> <span class="mtext">{{ composed.negative || '—' }}</span>
                <span class="tok" :class="{ over: negTokens > negLimit }" :title="`Negative prompt — ${negTokens} of ${negLimit} T5 tokens`">{{ negTokens }} / {{ negLimit }}</span></div>
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
            <NodeResizer v-if="!data.collapsed" :min-width="240" :min-height="260" :is-visible="selected"
              color="var(--accent)" />
            <PromptWidget :data="data" :selected="selected" :pins="paletteRows" :pinned-ids="pinnedIds"
              :revalidate="widgetRevalidate"
              @toggle="toggleLibraryCollapse" @open-library="emit('navigate', 'library')"
              @open-settings="emit('navigate', 'settings')" @pin="insertLibraryBlock($event)"
              @pin-many="$event.forEach(insertLibraryBlock)"
              @use="useLibraryBlock" @new-block="addCustomBlock" @copy="copyToStation" @unpin="unpinBlock"
              @save="saveToLibrary" @patch="patchPaletteNode" @reorder="reorderPalette" />
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
          <NodeResizer :min-width="176" :min-height="46" :is-visible="selected" color="var(--accent)" />
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
.gzgen{display:inline-flex;align-items:center;gap:8px;border:0;border-radius:var(--radius);background:var(--accent);color:var(--on-accent);font-weight:600;font-size:12px;padding:6px 14px;cursor:pointer}
.gzgen:disabled{opacity:.5;cursor:default}
.gzgen .gcost{font-size:11px;font-weight:700;background:color-mix(in srgb,#000 22%,transparent);color:#fff;padding:1px 7px;border-radius:20px}
.stbal{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:700;color:var(--text-dim);font-variant-numeric:tabular-nums}
.stbal .sttier{font-size:9.5px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;color:var(--accent);
  background:var(--nav-active);border:1px solid color-mix(in srgb,var(--accent) 35%,var(--border));border-radius:10px;padding:1px 6px}
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
.stmeta .mrow{display:flex;align-items:center;gap:8px;white-space:nowrap;margin-bottom:2px}
.stmeta .mrow b{color:var(--text-faint);flex-shrink:0}.stmeta .mrow.neg b{color:#e2483d}
.stmeta .mrow .mtext{overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}
.stmeta .tok{flex-shrink:0;color:var(--text-faint);font-weight:600;font-variant-numeric:tabular-nums}
.stmeta .tok.over{color:#e2483d}

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

.block{position:relative;width:100%;height:100%;min-height:46px;display:flex;flex-direction:column;border-radius:8px;border:1px solid var(--border);border-left:3px solid var(--cat);background:var(--surface-2);box-shadow:0 1px 4px rgba(0,0,0,.2);overflow:hidden}
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
