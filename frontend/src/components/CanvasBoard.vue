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
import { dedupePrompt } from '../canvas/dedup'
import PromptWidget from './PromptWidget.vue'
import GalleryWidget from './GalleryWidget.vue'
import BlockEditorModal from './BlockEditorModal.vue'
import { newId } from '../vault/ids'
import { onBeforeQuit } from '../electron'
import type { GenResult, LibraryBlock, PanelParams, PersistedComponent, SnapshotData, WorkDoc } from '../types'

const toast = useToast()
const { confirm } = useConfirm()
const props = defineProps<{
  drafts: GenResult[]; busy: boolean; error: string; preview: string; params: PanelParams
  openWork: WorkDoc | null
  keepDrafts?: { ids: string[]; nonce: number } | null
}>()
const emit = defineEmits<{
  generate: [{ positive: string; negative: string; snapshot: SnapshotData }]
  take: [string]
  cancel: []
  saved: [string]
  navigate: [string]
  'open-library': [{ category: string; tags: string[] }] // widget footer → Library, pre-filtered
  'new-work': []
}>()

const {
  nodes, addNodes, removeNodes, findNode, onNodeDragStop, getIntersectingNodes, viewport,
  screenToFlowCoordinate, setNodes, setViewport, onNodeContextMenu, onSelectionContextMenu, onPaneContextMenu,
} = useVueFlow()

// The "station" is one node: header + two zones (Generation output | Composition block list) + a meta
// footer. The Generation↔Composition ratio, split axis, and lead order live in the station node data.
// STATION/LIBRARY/GALLERY zone ids come from serialize (single source of truth). Content inside them is
// saved; anything loose on the canvas is a draft.
const ANCHORS = new Set([STATION, LIBRARY, GALLERY])

// Offline fallback colors (vault categories override once loaded); mirror catalog.py DEFAULTS.
const CATS: Record<string, string> = {
  character: '#0c66e4',
  body: '#c77d54', 'body-skin': '#e0a878', 'body-hair': '#9c6b3f', 'body-face': '#d99578', 'body-state': '#cf7a63',
  outfit: '#d4537e', 'outfit-fabric': '#c06a97', 'outfit-accessory': '#b3789e',
  pose: '#ae4787', action: '#9a5ba6', composition: '#7f5aa0',
  'scene-environment': '#1f845a', 'scene-lighting': '#b65c02', 'scene-camera': '#12b5a6', 'scene-effects': '#2f9e8f', 'scene-color': '#3f9d6b',
  style: '#6e5dc6',
  nsfw: '#c2255c', 'nsfw-act': '#a61e4d', 'nsfw-fluids': '#d6499a',
  negative: '#e2483d', custom: '#738496',
}
// Bumped whenever the vault's Library may have changed under the widget (returning to Generate,
// opening a work) so the prompt widget re-reads category colors/counts and its pins' versions —
// KeepAlive keeps the widget mounted, so it can't rely on its own onMounted firing again.
const widgetRevalidate = ref(0)
// Colors + names come from the vault's categories (customs have their own); CATS is the offline fallback.
const vaultCatColors = ref<Record<string, string>>({})
const vaultCatNames = ref<Record<string, string>>({})
const vaultCatOrder = ref<string[]>([]) // slugs in the vault's user-defined order (shared everywhere)
async function loadCategoryColors() {
  try {
    const cats = await listCategories()
    vaultCatColors.value = Object.fromEntries(cats.map((c) => [c.slug, c.color]))
    vaultCatNames.value = Object.fromEntries(cats.map((c) => [c.slug, c.name]))
    vaultCatOrder.value = cats.map((c) => c.slug)
  } catch { /* backend not ready / no vault — fall back to the builtin palette */ }
}
const catColor = (c: string) => vaultCatColors.value[c] ?? CATS[c] ?? CATS.custom
const catName = (c: string) => vaultCatNames.value[c] || (c.startsWith('cat-') ? 'custom' : c)
// Rank a category by its position in the vault order (unknown slugs sort to the end).
const catRank = (slug: string) => { const i = vaultCatOrder.value.indexOf(slug); return i < 0 ? vaultCatOrder.value.length : i }

// Estimated Anlas cost of the current params (Opus tier gets the first sample free — see cost.ts).
const { subscription } = useAccount()
const genCost = computed(() => anlasCost(props.params, subscription.value?.tier ?? 0, !!subscription.value?.active))

// Composition = the station's child block nodes, ordered by position.y (the list order key, like the
// old palette). They never free-render on the canvas — the station node draws them as an HTML list.
const compBlocks = computed(() => nodes.value.filter((n) => n.type === 'block' && n.parentNode === STATION)
  .slice().sort((a, b) => a.position.y - b.position.y))
const composed = computed(() => {
  const pick = (neg: boolean) => compBlocks.value
    .filter((b) => !b.data.frozen && (b.data.polarity === 'negative') === neg) // frozen blocks stay in the area but out of the prompt
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

const flowRef = ref<HTMLElement | null>(null)
const topSelected = ref(false)

// Image-rendering pipeline (decode sizing, ?w= thumbnails, flash-free swap, reference scaling). `dims` and
// `sizeOf` are hoisted node-size helpers shared with arrange/settle; the pipeline gets them by reference.
const { shownSrc, scaleOf, imgScale, fullStyle, imgSrc, seedSrc, swapSrc, applyScale } =
  useImagePipeline({ nodes, findNode, sizeOf, dims })

// Per-work quick-access set (Library block ids), persisted on the WorkDoc. Loaded in loadDoc, toggled
// by the widget's ★, and fed to autosave so a star change marks the work dirty. Defined before
// useAutosave so its closure captures an initialised ref.
const favorites = ref<string[]>([])

// Vault autosave (dirty flag, flush, save state). Owns the state + logic; the lifecycle (window listeners,
// the periodic timer, and the KeepAlive activate/deactivate hooks) is wired in onMounted/onUnmounted below.
const { title, vaultReady, workId, saveState, savedAt, markDirty, flush, flushIfDirty, manualSave,
  onBeforeUnload, refreshInterval, stopAutosave, resetBaseline } = useAutosave({
  nodes, viewport, params: () => props.params, drafts: () => props.drafts, favorites: () => favorites.value,
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
  if (payload.startsWith('nai-libblock:')) { // a widget row dragged out → copy of the vault block
    try {
      const b = JSON.parse(payload.slice('nai-libblock:'.length)) as LibraryBlock
      dropBlockAt(libraryBlockData(b), toFlow(e.clientX, e.clientY))
    } catch { /* malformed payload — ignore */ }
    return
  }
  if (payload.startsWith('nai-comp:')) { // a composition row dragged out of the station → MOVE it loose onto the canvas
    const src = findNode(payload.slice('nai-comp:'.length))
    if (src && src.type === 'block') {
      const pos = toFlow(e.clientX, e.clientY)
      src.parentNode = undefined
      src.hidden = false
      src.data = { ...src.data, expanded: false, editing: false }
      src.position = { x: pos.x - 88, y: pos.y - 17 }
      src.style = { width: '176px' }
      nudgeIfOverlapping(src)
    }
    return
  }
  if (payload.startsWith('nai-galimg:')) { // a gallery thumbnail dragged out → MOVE it to the canvas as scratch
    moveGalleryImageToScratch(payload.slice('nai-galimg:'.length), toFlow(e.clientX, e.clientY))
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
    // Kept to the gallery → a hidden child in the widget's Quick access (unassigned to any grid until curated).
    addNodes([{ id, type: 'image', parentNode: GALLERY, hidden: true, zIndex: 3, style: { width: `${w}px`, height: `${h}px` }, position: { x: 0, y: 0 }, data }])
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
      // Kept to the gallery → hidden children in the widget's Quick access (unassigned until curated).
      addNodes([{ id: did, type: 'image', parentNode: GALLERY, hidden: true, zIndex: 3, style: { width: `${w}px`, height: `${h}px` }, position: { x: 0, y: 0 }, data }])
    } else {
      const base = pos ?? { x: 60, y: 60 }
      addNodes([{ id: did, type: 'image', zIndex: 3, style: { width: `${w}px`, height: `${h}px` }, position: { x: base.x - w / 2 + i, y: base.y - h / 2 + i }, data }])
    }
    seedSrc(did)
    placed.push(did)
  })
  if (!placed.length) return
  nextTick(() => {
    // Gallery images are hidden children (the widget lays them out) — only scratch drops need arranging.
    if (!(inGallery && gal)) {
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
  const sel = nodes.value.filter((n) => n.selected && !n.hidden) // hidden = gallery child; no picker over it (item 5)
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
  const imgs = nodes.value.filter((n) => n.type === 'image' && n.selected && !n.hidden)
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

/* ============================ Composition (station block list) ============================ */
// The station renders its child blocks as an ordered HTML list (order = position.y); they stay hidden
// so Vue Flow never free-renders them on the canvas.
const compFilter = ref('') // single-select category rail; '' = all
const compRows = computed(() => {
  let rows = compBlocks.value.filter((n) => !compFilter.value || n.data.category === compFilter.value)
  // In "All" group by the vault category order (then manual position.y within a category); a filtered
  // category keeps pure position.y. Drag-reorder stays enabled in both (manual order within a category).
  if (!compFilter.value) {
    rows = rows.slice().sort((a, b) => catRank(a.data.category) - catRank(b.data.category) || (a.position.y - b.position.y))
  }
  return rows.map((n) => ({
    nodeId: n.id,
    name: (n.data?.name as string) || 'Untitled',
    text: (n.data?.text as string) || '',
    polarity: (((n.data?.polarity as string) === 'negative') ? 'negative' : 'positive') as 'positive' | 'negative',
    category: (n.data?.category as string) || 'custom',
    block_id: n.data?.block_id as string | undefined,
    expanded: !!n.data?.expanded,
    frozen: !!n.data?.frozen,
  }))
})
// Rail categories derived from the composition (only categories present), with counts + colors.
const compRail = computed(() => {
  const counts: Record<string, number> = {}
  for (const b of compBlocks.value) counts[b.data.category] = (counts[b.data.category] || 0) + 1
  return Object.entries(counts).sort((a, b) => catRank(a[0]) - catRank(b[0])) // vault order, not by count
    .map(([slug, count]) => ({ slug, name: catName(slug), color: catColor(slug), count }))
})

function hideStationBlocks() {
  for (const n of nodes.value) if (n.parentNode === STATION && n.type === 'block') n.hidden = true
}
// Gallery images render only inside the structured gallery widget's grids — the nodes stay (their
// domain is WorkDoc.images) but are hidden on the canvas, like station composition blocks.
function hideGalleryImages() {
  for (const n of nodes.value) if (n.parentNode === GALLERY && n.type === 'image') n.hidden = true
}
// The gallery's image nodes, oldest→newest, handed to the GalleryWidget for its grid queries.
const galleryImages = computed(() =>
  nodes.value.filter((n) => n.type === 'image' && n.parentNode === GALLERY)
    .sort((a, b) => String(a.data?.created_at || '').localeCompare(String(b.data?.created_at || ''))),
)
function toggleImageFavorite(id: string) {
  const n = findNode(id)
  if (n && n.type === 'image') n.data.favorite = !n.data.favorite // mutation → tracked + autosaved
}
// ---- gallery grid albums: each gallery image belongs to exactly one grid (data.blocks) ----
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function galleryGrids(): any[] {
  return (((findNode(GALLERY)?.data.blocks as any[]) || []).filter((b) => b.type === 'grid'))
}
function removeImageFromGridAlbums(id: string) {
  for (const g of galleryGrids()) { const i = g.imageIds.indexOf(id); if (i >= 0) g.imageIds.splice(i, 1) }
}
// A thumb or kept draft dropped onto a specific grid → joins THAT album (not always the first one).
function onGalleryGridDrop(p: { gridId: string; imageId?: string; payload?: string }) {
  const grid = galleryGrids().find((g) => g.id === p.gridId)
  if (!grid) return
  if (p.imageId) { // an existing gallery thumbnail moved into this album
    const n = findNode(p.imageId)
    if (!n || n.type !== 'image') return
    removeImageFromGridAlbums(p.imageId)
    if (!grid.imageIds.includes(p.imageId)) grid.imageIds.push(p.imageId)
    n.parentNode = GALLERY; n.hidden = true; n.position = { x: 0, y: 0 } // ensure it's a gallery child
    return
  }
  keepDraftsToGallery(p.payload || '', grid) // a kept draft dropped onto this grid → materialise it here
}
// Materialise kept drafts (Output/Stack payload) as gallery children; add to `grid` if given, else
// leave them unassigned (Quick access).
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function keepDraftsToGallery(pl: string, grid?: any) {
  const ids = pl === 'nai-draft' ? (props.drafts[0] ? [props.drafts[0].id] : [])
    : pl.startsWith('nai-draft:') ? [pl.slice('nai-draft:'.length)]
      : pl.startsWith('nai-drafts:') ? pl.slice('nai-drafts:'.length).split(',') : []
  for (const did of ids) {
    const draft = props.drafts.find((d) => d.id === did)
    if (!draft) continue
    const ar = (draft.params.width || 832) / (draft.params.height || 1216)
    const { w, h } = spawnSize(ar)
    const data = { url: draft.url, file: draft.file || '', snapshot: draft.snapshot, ar, created_at: draft.created_at || new Date().toISOString() }
    addNodes([{ id: did, type: 'image', parentNode: GALLERY, hidden: true, zIndex: 3, style: { width: `${w}px`, height: `${h}px` }, position: { x: 0, y: 0 }, data }])
    if (grid && !grid.imageIds.includes(did)) grid.imageIds.push(did)
    seedSrc(did)
    emit('take', did)
  }
}
// Dropped onto Quick access → an unassigned gallery image (a thumb leaves its grid; a draft materialises loose).
function onGalleryQuickDrop(p: { imageId?: string; payload?: string }) {
  if (p.imageId) {
    const n = findNode(p.imageId)
    if (!n || n.type !== 'image') return
    removeImageFromGridAlbums(p.imageId)
    n.parentNode = GALLERY; n.hidden = true; n.position = { x: 0, y: 0 }
    return
  }
  keepDraftsToGallery(p.payload || '')
}

// Delete a gallery image from the work entirely (confirmed — it's the user's content and unrecoverable).
async function onGalleryDeleteImg(id: string) {
  const n = findNode(id)
  if (!n || n.type !== 'image') return
  if (!(await confirm({
    title: 'Delete image', danger: true, confirmLabel: 'Delete',
    message: 'Remove this image from the work? This cannot be undone.',
  }))) return
  removeImageFromGridAlbums(id)
  removeNodes([id])
}

// Delete every unsorted (Quick access) image except favourites — confirmed, it's the user's content.
async function onGalleryClearQuick(ids: string[]) {
  if (!ids.length) return
  if (!(await confirm({
    title: 'Clear Quick access', danger: true, confirmLabel: `Delete ${ids.length}`,
    message: `Delete ${ids.length} unsorted image${ids.length === 1 ? '' : 's'} from the work? Favourited images are kept. This cannot be undone.`,
  }))) return
  removeNodes(ids)
}

// Remove a gallery image from its album and float it back onto the canvas as scratch (a move, not a copy).
function moveGalleryImageToScratch(id: string, pos?: { x: number; y: number }) {
  const n = findNode(id)
  if (!n || n.type !== 'image') return
  removeImageFromGridAlbums(id)
  const { w, h } = spawnSize((n.data.ar as number) || 3 / 4)
  const base = pos ?? { x: (findNode(GALLERY)?.position.x ?? 0) - w - 60, y: (findNode(GALLERY)?.position.y ?? 0) + 40 }
  n.parentNode = undefined
  n.hidden = false
  n.position = { x: base.x - (pos ? w / 2 : 0), y: base.y - (pos ? h / 2 : 0) }
  nudgeIfOverlapping(n)
  seedSrc(id)
}
const nextCompY = () => (compBlocks.value.length ? Math.max(...compBlocks.value.map((n) => n.position.y)) + 10 : 0)

// Edit a composition row via property mutation (Vue Flow tracks node.data mutations, not reassignment).
function patchCompBlock(p: { nodeId: string; patch: Record<string, unknown> }) {
  const n = findNode(p.nodeId)
  if (n && n.parentNode === STATION) Object.assign(n.data, p.patch)
}
function toggleCompExpand(nodeId: string) {
  const n = findNode(nodeId)
  if (n) n.data.expanded = !n.data.expanded
}
function toggleCompPolarity(nodeId: string) {
  const n = findNode(nodeId)
  if (n) n.data.polarity = n.data.polarity === 'negative' ? 'positive' : 'negative'
}
// Freeze: keep the block in the area but exclude it from the assembled prompt (and the snapshot).
function toggleCompFreeze(nodeId: string) {
  const n = findNode(nodeId)
  if (n) n.data.frozen = !n.data.frozen
}
function deleteCompBlock(nodeId: string) { removeNodes([nodeId]) }

// Inline rename of a composition row (double-click the name).
const renamingId = ref<string | null>(null)
function startCompRename(nodeId: string) {
  renamingId.value = nodeId
  requestAnimationFrame(() => {
    const el = document.getElementById(`cname-${nodeId}`) as HTMLInputElement | null
    el?.focus(); el?.select()
  })
}

// Clear the whole composition (confirmed — it's the work's prompt).
async function clearComposition() {
  const ids = compBlocks.value.map((n) => n.id)
  if (!ids.length) return
  if (!(await confirm({
    title: 'Clear composition', danger: true, confirmLabel: 'Clear all',
    message: `Remove all ${ids.length} block${ids.length > 1 ? 's' : ''} from the generation area?`,
  }))) return
  removeNodes(ids)
}

// Auto-size an expanded prompt textarea to fit its content (grows on open + while typing).
function autosize(el: HTMLTextAreaElement) { el.style.height = 'auto'; el.style.height = `${Math.max(52, el.scrollHeight)}px` }
const vAutosize = { mounted: (el: HTMLTextAreaElement) => autosize(el) }

// Interacting with a composition row/rail must not select/drag the whole station node — stop the
// press from reaching Vue Flow, but only when it started on an interactive element (rows/buttons/inputs).
function stopIfInteractive(e: Event) {
  const t = e.target as HTMLElement | null
  if (t && t !== e.currentTarget && t.closest('button, input, textarea, .crow, .cnav, .clearall')) e.stopPropagation()
}

// Drag-reorder within the list: renumber position.y as 0,10,20… after moving nodeId before beforeId.
function reorderComp(p: { nodeId: string; beforeId: string | null }) {
  const ids = compBlocks.value.map((n) => n.id)
  const rest = ids.filter((id) => id !== p.nodeId)
  const at = p.beforeId ? rest.indexOf(p.beforeId) : -1
  rest.splice(at >= 0 ? at : rest.length, 0, p.nodeId)
  rest.forEach((id, i) => { const n = findNode(id); if (n) n.position = { x: 0, y: i * 10 } })
}

// ---- station layout (two zones): ratio, axis (h/v), genFirst ----
function setStationAxis(axis: 'h' | 'v') { const st = findNode(STATION); if (st) st.data.axis = axis }
function swapStationZones() { const st = findNode(STATION); if (st) st.data.genFirst = !(st.data.genFirst ?? true) }

// Drag a composition row: within the list = reorder; out onto the canvas = an independent scratch copy.
const compDragId = ref<string | null>(null)
const compDropBefore = ref<string | null | undefined>(undefined)
function onCompDragStart(e: DragEvent, nodeId: string) {
  if (!e.dataTransfer) return
  compDragId.value = nodeId
  e.dataTransfer.effectAllowed = 'copyMove'
  e.dataTransfer.setData('text/plain', `nai-comp:${nodeId}`)
}
function onCompDragOver(e: DragEvent, nodeId: string) {
  if (!compDragId.value || compDragId.value === nodeId) return
  e.preventDefault()
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const before = e.clientY < r.top + r.height / 2
  const idx = compRows.value.findIndex((p) => p.nodeId === nodeId)
  compDropBefore.value = before ? nodeId : (compRows.value[idx + 1]?.nodeId ?? null)
}
function onCompDrop(e: DragEvent) {
  if (!compDragId.value || compDropBefore.value === undefined) return
  e.preventDefault()
  e.stopPropagation() // don't let the canvas drop handler spawn a scratch copy
  reorderComp({ nodeId: compDragId.value, beforeId: compDropBefore.value })
  compDragId.value = null
  compDropBefore.value = undefined
}
function onCompDragEnd() { compDragId.value = null; compDropBefore.value = undefined }

// The three anchor zones only — the skeleton every work starts from.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
// Default sizes are generous (the reworked widgets pack a rail + list) and the three anchors are laid
// out with clear gaps so a fresh work never opens with them cramped/overlapping. FRESH_VIEWPORT frames
// the whole spread.
const FRESH_VIEWPORT = { x: 28, y: 24, zoom: 0.7 }
function zoneNodes(): any[] {
  return [
    { id: LIBRARY, type: 'zone', position: { x: 40, y: 40 }, data: { role: 'library' }, zIndex: 0, style: { width: '440px', height: '680px' } },
    { id: STATION, type: 'station', position: { x: 520, y: 40 }, data: { ratio: 0.3, axis: 'h', genFirst: true }, zIndex: 0, style: { width: '1080px', height: '680px' } },
    { id: GALLERY, type: 'zone', position: { x: 1640, y: 40 }, data: { role: 'gallery', blocks: [{ id: newId('gb'), type: 'grid', source: 'all', cols: 3 }] }, zIndex: 0, style: { width: '760px', height: '640px' } },
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
  setViewport(FRESH_VIEWPORT)
  resetBaseline('idle')
  emit('new-work') // GenerateView re-seeds params from the default preset
}

onMounted(async () => {
  await checkVault()
  if (props.openWork) loadDoc(props.openWork)
  else { addNodes(zoneNodes()); setViewport(FRESH_VIEWPORT) } // fresh start → spaced anchor zones, framed

})
watch(() => props.openWork, (w) => { if (w) loadDoc(w) })

// Settle EVERY dragged node (multi-select moves a whole set) — not just the grabbed one.
onNodeDragStop(({ nodes: dragged, node, event }) => {
  const set = dragged && dragged.length ? dragged : [node]
  // Drop-pointer screen coords let a single image land in the exact grid it was dropped over (item 2).
  const ev = event as MouseEvent | undefined
  const dropXY = set.length === 1 && ev && typeof ev.clientX === 'number' ? { x: ev.clientX, y: ev.clientY } : undefined
  for (const n of set) settleNode(n, dropXY)
})

// Which gallery grid (if any) sits under a screen point — hit-tests the widget DOM beneath the dragged
// node (elementsFromPoint returns the dragged node too; skip it to the grid below). Enables canvas→grid.
function gridUnderPoint(p: { x: number; y: number } | undefined): string | null {
  if (!p) return null
  for (const el of document.elementsFromPoint(p.x, p.y)) {
    const g = (el as HTMLElement).closest?.('.b-grid[data-gid]')
    if (g) return g.getAttribute('data-gid')
  }
  return null
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function settleNode(node: any, dropXY?: { x: number; y: number }) {
  const live = findNode(node.id)
  if (!live) return
  const overStation = getIntersectingNodes(node).some((n) => n.id === STATION)

  if (node.type === 'block') {
    // Only loose scratch blocks are draggable (composition blocks are hidden list rows). Dropping one
    // onto the station appends it to the composition list; dropping it elsewhere keeps it loose.
    if (overStation) {
      live.parentNode = STATION
      live.hidden = true
      live.position = { x: 0, y: nextCompY() }
      live.data = { ...live.data, expanded: false }
    } else if (live.parentNode) {
      live.position = { x: node.computedPosition.x, y: node.computedPosition.y }
      live.parentNode = undefined
    }
  } else if (node.type === 'image') {
    const gal = getIntersectingNodes(node).find((n) => n.type === 'zone' && n.data.role === 'gallery')
    if (gal) {
      // Into the gallery → a hidden gallery child. If dropped precisely over a grid, join THAT album
      // (item 2); otherwise it lands unassigned in Quick access. Deselect so no scale-picker lingers (item 5).
      live.parentNode = gal.id
      live.hidden = true
      live.selected = false
      const gid = gridUnderPoint(dropXY)
      if (gid) { const grid = galleryGrids().find((g) => g.id === gid); if (grid && !grid.imageIds.includes(live.id)) grid.imageIds.push(live.id) }
    } else if (live.parentNode) {
      live.position = { x: node.computedPosition.x, y: node.computedPosition.y }
      live.parentNode = undefined
      live.hidden = false // dragged back out of the gallery → a visible scratch image again
      removeImageFromGridAlbums(live.id)
      nudgeIfOverlapping(live)
    }
  }
}

// ---- prompt widget (library zone): direct Library browser + per-work favorites ----
// The widget browses the whole vault directly (no palette pins). ⇢ / drag / ＋ New block each drop an
// independent block into the station; the only per-work state the widget owns is `favorites` (Library
// block ids), persisted on the WorkDoc. The zone's collapse state is layout on the zone node.
function toggleFavorite(blockId: string) {
  const i = favorites.value.indexOf(blockId)
  if (i >= 0) favorites.value.splice(i, 1)
  else favorites.value.push(blockId)
}

// Collapse persists with the work (layout layer). Height must be written to BOTH the node's
// numeric size (what NodeResizer mutates — a stale style.height would otherwise be ignored) and
// its style, or the expanded height won't restore.
function setZoneHeight(zone: any, h: number) {
  zone.style = { ...(zone.style as object), height: `${h}px` }
  zone.height = h
  if (zone.dimensions) zone.dimensions = { ...zone.dimensions, height: h }
}
// ---- copy semantics: every block that enters the work is an independent copy ----
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
// Identities (polarity + text) currently in the composition — the Library widget greys these out.
const usedBlockKeys = computed(() => new Set(compBlocks.value.map((n) => blockKey(n.data?.polarity, n.data?.text))))

// Drop from the Library widget (drag-out): an independent copy joins the composition when dropped
// over the station, else lands loose on the canvas as scratch.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function dropBlockAt(data: any, pos: { x: number; y: number }) {
  const st = findNode(STATION)
  if (st) {
    const stPos = st.computedPosition || st.position
    const { w: stW, h } = dims(st)
    if (pos.x >= stPos.x && pos.x <= stPos.x + stW && pos.y >= stPos.y && pos.y <= stPos.y + h) {
      appendToComp(data)
      return
    }
  }
  const bw = 176, bh = 34
  addNodes([{
    id: newId('blk'), type: 'block', zIndex: 2,
    position: { x: pos.x - bw / 2, y: pos.y - bh / 2 }, style: { width: `${bw}px` },
    data,
  }])
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function libraryBlockData(b: LibraryBlock): any {
  return { category: b.category, name: b.name, text: b.text, polarity: b.polarity,
    block_id: b.id, version: b.version ?? 1, tags: [...(b.tags ?? [])], expanded: false }
}

// ⇢ / drop / ＋: an independent copy appended to the END of the composition list (position.y order).
// Polarity is the block's own (no lanes); duplicates (same polarity + text) are refused, not stacked.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function appendToComp(data: any) {
  if (!findNode(STATION)) return
  if (stationHasBlock(data.polarity ?? 'positive', data.text)) { warnDuplicate(); return }
  addNodes([{
    id: newId('blk'), type: 'block', parentNode: STATION, zIndex: 2, hidden: true,
    position: { x: 0, y: nextCompY() }, style: { width: '176px' },
    data: { ...data, expanded: false },
  }])
}
function useLibraryBlock(b: LibraryBlock) {
  appendToComp(libraryBlockData(b))
}

// ＋ New block (from the Library widget): a fresh work-local block appended to the composition.
function newCompBlock(category: string) {
  appendToComp({ category: category || 'custom', name: 'Untitled', text: '', polarity: 'positive', tags: [] })
}

// ↥ Save a station block to the vault. The shared editor opens in-place (no tab switch); on save
// the block adopts the returned vault ref (block_id/version/tags) so a later re-save carries it.
const editingDraft = ref<{ nodeId: string; block: LibraryBlock } | null>(null)
function saveToLibrary(nodeId: string) {
  const live = findNode(nodeId)
  if (!live || live.type !== 'block') return
  if (!String(live.data.text || '').trim()) {
    toast.push('Add prompt text before saving to the Library', 'err')
    return
  }
  editingDraft.value = {
    nodeId,
    block: {
      id: newId('block'), category: live.data.category || 'custom', name: live.data.name || '',
      text: live.data.text, polarity: live.data.polarity || 'positive', tags: [...(live.data.tags || [])],
    },
  }
}
function onDraftSaved(block: LibraryBlock) {
  const link = editingDraft.value
  editingDraft.value = null
  if (!link) return
  const n = findNode(link.nodeId)
  if (!n || n.type !== 'block') return
  n.data = {
    ...n.data, block_id: block.id, version: block.version ?? 1,
    category: block.category, name: block.name, text: block.text,
    polarity: block.polarity, tags: [...block.tags],
  }
}

function doGenerate() {
  const components = compBlocks.value.filter((b) => !b.data.frozen).map((b): PersistedComponent => ({ // frozen blocks are excluded; compBlocks is in list order (position.y)
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
  favorites.value = [...(doc.favorites || [])] // per-work quick-access set for the widget's ★ filter
  hideStationBlocks() // composition blocks render as the station's list, never free on the canvas
  hideGalleryImages() // gallery images render inside the structured gallery widget, not free on the canvas
  widgetRevalidate.value++ // a freshly opened work re-reads the Library (categories/counts)
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
    { label: 'Move to Quick access', icon: '⤒', onClick: () => keepDraftsBatch([d.id]) },
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

// Drag the single splitter to adjust the Generation↔Composition ratio (axis-aware; inverts when the
// generation zone leads from the far side).
function startSplit(e: MouseEvent) {
  const st = findNode(STATION)
  if (!st) return
  const vert = (st.data.axis ?? 'h') === 'v'
  const genFirst = st.data.genFirst ?? true
  const start = vert ? e.clientY : e.clientX
  const startRatio = st.data.ratio ?? 0.3
  const sd = dims(st) // style fallback → safe if the station isn't currently measured
  const span = vert ? sd.h : sd.w
  const onMove = (ev: MouseEvent) => {
    const zoom = viewport.value?.zoom ?? 1
    let delta = ((vert ? ev.clientY : ev.clientX) - start) / zoom / Math.max(1, span)
    if (!genFirst) delta = -delta
    st.data.ratio = Math.min(0.8, Math.max(0.2, startRatio + delta))
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
          <NodeResizer :min-width="560" :min-height="320" :is-visible="selected" color="var(--accent)" />
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

            <!-- two zones: Generation | Composition — axis + lead order come from data.axis/genFirst -->
            <div class="stbody" :class="{ v: (data.axis ?? 'h') === 'v', rev: !(data.genFirst ?? true) }">
              <div class="genzone" :style="{ flexGrow: data.ratio ?? 0.3 }">
                <div class="zlbl">Output</div>
                <div class="outbody">
                  <img v-if="busy && preview" class="liveprev" :src="preview" alt="generating preview" />
                  <div v-else-if="drafts.length" class="topwrap nodrag" :class="{ selected: topSelected }" draggable="true"
                    @dragstart="onDraftDragStart" @pointerdown.stop @click.stop="topSelected = !topSelected"
                    @contextmenu.stop="onStationMenu" title="Drag onto the canvas to keep · right-click for actions">
                    <img class="topimg" :src="drafts[0].url" alt="latest generation" draggable="false" />
                    <span v-if="drafts[0].mock" class="mockbadge" title="Offline placeholder — no NovelAI token set">MOCK</span>
                    <span class="stackbadge">{{ drafts.length }} in stack</span>
                    <span class="draghint">⤴ drag to keep</span>
                    <div class="topacts nodrag">
                      <button class="tact" title="Preview" @click.stop="openPreview(drafts[0].url)" @pointerdown.stop>⤢</button>
                      <button class="tact" title="Move to gallery Quick access" @click.stop="keepDraftsBatch([drafts[0].id])" @pointerdown.stop>⤒</button>
                      <button class="tact del" title="Remove from stack" @click.stop="$emit('take', drafts[0].id)" @pointerdown.stop>🗑</button>
                    </div>
                  </div>
                  <span v-else-if="!busy" class="outhint">Generated images appear here — drag them out to keep.</span>
                </div>
              </div>

              <div class="stsplit nodrag" title="Drag to resize" @mousedown.stop.prevent="startSplit($event)"></div>

              <div class="compzone" :style="{ flexGrow: 1 - (data.ratio ?? 0.3) }">
                <div class="complist nodrag nowheel" @pointerdown="stopIfInteractive" @mousedown="stopIfInteractive"
                  @click="stopIfInteractive" @drop="onCompDrop" @dragover.prevent>
                  <div v-if="!compRows.length" class="comphint">
                    {{ compFilter ? `No ${catName(compFilter)} blocks.` : 'Drop blocks from the Library widget to build the prompt.' }}
                  </div>
                  <div v-for="b in compRows" :key="b.nodeId" class="crow"
                    :class="[b.frozen ? 'frozen' : (b.polarity === 'negative' ? 'neg' : 'pos'), { drop: compDropBefore === b.nodeId }]"
                    draggable="true" @dragstart="onCompDragStart($event, b.nodeId)" @dragover="onCompDragOver($event, b.nodeId)" @dragend="onCompDragEnd">
                    <div class="cr1">
                      <span class="grip" title="Drag to reorder or out to the canvas">⠿</span>
                      <span class="cdot2" :style="{ background: catColor(b.category) }" :title="catName(b.category)"></span>
                      <input v-if="renamingId === b.nodeId" :id="`cname-${b.nodeId}`" class="cnamein nodrag" :value="b.name"
                        @pointerdown.stop @click.stop @input="patchCompBlock({ nodeId: b.nodeId, patch: { name: ($event.target as HTMLInputElement).value } })"
                        @blur="renamingId = null" @keyup.enter="renamingId = null" @keyup.esc="renamingId = null" />
                      <span v-else class="cname" title="Double-click to rename" @dblclick.stop="startCompRename(b.nodeId)">{{ b.name }}</span>
                      <span class="crsp"></span>
                      <button class="cicon frz" :class="{ on: b.frozen }" :title="b.frozen ? 'Frozen — kept in the area but not sent to the prompt; click to unfreeze' : 'Freeze — keep in the area but exclude from the prompt'"
                        @click.stop="toggleCompFreeze(b.nodeId)" @pointerdown.stop>❄</button>
                      <button class="cicon pol" :class="b.polarity === 'negative' ? 'neg' : 'pos'" :title="`Polarity: ${b.polarity} — click to flip`"
                        @click.stop="toggleCompPolarity(b.nodeId)" @pointerdown.stop>{{ b.polarity === 'negative' ? '−' : '＋' }}</button>
                      <button class="cicon" :title="b.expanded ? 'Collapse' : 'Expand to edit'"
                        @click.stop="toggleCompExpand(b.nodeId)" @pointerdown.stop>{{ b.expanded ? '▾' : '▸' }}</button>
                      <button class="cicon" title="Save to Library…" @click.stop="saveToLibrary(b.nodeId)" @pointerdown.stop>↥</button>
                      <button class="cicon del" title="Delete from composition" @click.stop="deleteCompBlock(b.nodeId)" @pointerdown.stop>✕</button>
                    </div>
                    <textarea v-if="b.expanded" v-autosize class="ctext nodrag nowheel" :value="b.text" placeholder="tags…"
                      @pointerdown.stop @click.stop
                      @input="patchCompBlock({ nodeId: b.nodeId, patch: { text: ($event.target as HTMLTextAreaElement).value } }); autosize($event.target as HTMLTextAreaElement)"></textarea>
                    <div v-else class="cprev">{{ b.text || 'empty' }}</div>
                  </div>
                </div>
                <div class="comprail nodrag nowheel" @pointerdown="stopIfInteractive" @mousedown="stopIfInteractive" @click="stopIfInteractive">
                  <div class="railnavs">
                    <button class="cnav" :class="{ on: !compFilter }" @click.stop="compFilter = ''" @pointerdown.stop>
                      <span class="gl">▦</span><span class="cn">All</span><span class="cc">{{ compBlocks.length }}</span>
                    </button>
                    <button v-for="c in compRail" :key="c.slug" class="cnav" :class="{ on: compFilter === c.slug }"
                      @click.stop="compFilter = c.slug" @pointerdown.stop>
                      <span class="cdot2" :style="{ background: c.color }"></span><span class="cn">{{ c.name }}</span><span class="cc">{{ c.count }}</span>
                    </button>
                  </div>
                  <button v-if="compBlocks.length" class="clearall" title="Remove every block from the composition"
                    @click.stop="clearComposition" @pointerdown.stop>🗑 Clear all</button>
                </div>
              </div>
            </div>

            <div class="stmeta nowheel">
              <div class="mrows">
                <div class="mrow"><b>+</b> <span class="mtext">{{ composed.positive || '—' }}</span>
                  <span class="tok" :class="{ over: posTokens > posLimit }" :title="`Positive prompt — ${posTokens} of ${posLimit} T5 tokens`">{{ posTokens }} / {{ posLimit }}</span></div>
                <div class="mrow neg"><b>−</b> <span class="mtext">{{ composed.negative || '—' }}</span>
                  <span class="tok" :class="{ over: negTokens > negLimit }" :title="`Negative prompt — ${negTokens} of ${negLimit} T5 tokens`">{{ negTokens }} / {{ negLimit }}</span></div>
              </div>
              <div class="orient nodrag" title="Zone layout">
                <button :class="{ on: (data.axis ?? 'h') === 'h' }" title="Side by side" @click.stop="setStationAxis('h')" @pointerdown.stop>⇔</button>
                <button :class="{ on: (data.axis ?? 'h') === 'v' }" title="Stacked" @click.stop="setStationAxis('v')" @pointerdown.stop>⇕</button>
                <button title="Swap which zone leads" @click.stop="swapStationZones" @pointerdown.stop>⇄</button>
              </div>
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
            <NodeResizer :min-width="340" :min-height="280" :is-visible="selected" color="var(--accent)" />
            <PromptWidget :data="data" :selected="selected" :favorites="favorites" :revalidate="widgetRevalidate" :used-keys="usedBlockKeys"
              @open-library="emit('open-library', $event)"
              @open-settings="emit('navigate', 'settings')"
              @use="useLibraryBlock" @new-block="newCompBlock" @toggle-favorite="toggleFavorite" />
          </template>
          <template v-else>
            <NodeResizer :min-width="360" :min-height="280" :is-visible="selected" color="var(--accent)" />
            <GalleryWidget :data="data" :images="galleryImages" :selected="selected" @favorite="toggleImageFavorite" @preview="openPreview" @to-quick="(id) => onGalleryQuickDrop({ imageId: id })" @delete-img="onGalleryDeleteImg" @clear-quick="onGalleryClearQuick" @download="downloadImages" @drop-on-grid="onGalleryGridDrop" @drop-on-quick="onGalleryQuickDrop" />
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
              <button v-if="!data.block_id" class="bicon nodrag" title="Save to Library…" @click="saveToLibrary(id)">↥</button>
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

    <!-- Save-to-Library opens the shared editor in-place (no tab switch). -->
    <BlockEditorModal v-if="editingDraft" :block="editingDraft.block" :is-new="true"
      @close="editingDraft = null" @saved="onDraftSaved" />
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
/* two zones — axis (v = stacked) + lead order (rev = the generation zone leads from the far side) */
.stbody{flex:1;display:flex;min-height:0}
.stbody.v{flex-direction:column}
.stbody.rev:not(.v){flex-direction:row-reverse}
.stbody.rev.v{flex-direction:column-reverse}
.genzone{flex-basis:0;min-width:80px;min-height:0;display:flex;flex-direction:column;background:color-mix(in srgb,var(--surface-2) 40%,transparent)}
.zlbl{height:24px;flex-shrink:0;display:flex;align-items:center;padding:0 12px;font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint)}
.outbody{flex:1;min-height:0;position:relative}
.stsplit{flex-shrink:0;background:var(--border)}
.stbody:not(.v) .stsplit{width:6px;cursor:col-resize}
.stbody.v .stsplit{height:6px;cursor:row-resize}
.stsplit:hover{background:var(--accent)}
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
/* explicit action buttons duplicating the right-click menu on the generation preview */
.topacts{position:absolute;top:6px;left:6px;display:flex;gap:4px;opacity:0;transition:opacity .12s}
.topwrap:hover .topacts{opacity:1}
.tact{width:24px;height:24px;border:1px solid var(--border-strong);border-radius:6px;background:color-mix(in srgb,#000 55%,var(--surface-1));color:#fff;font-size:12px;line-height:1;cursor:pointer;padding:0;display:flex;align-items:center;justify-content:center}
.tact:hover{border-color:var(--accent);color:var(--accent)}
.tact.del:hover{border-color:var(--danger,#e2483d);color:var(--danger,#e2483d)}
/* composition zone = [ block list | category rail on the right ] */
.compzone{flex-basis:0;min-width:0;min-height:0;display:flex;border-left:1px solid var(--border)}
.stbody.rev:not(.v) .compzone{border-left:0;border-right:1px solid var(--border)}
.stbody.v .compzone{border-left:0;border-top:1px solid var(--border)}
.complist{flex:1;min-width:0;min-height:0;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:5px}
.comphint{margin:auto;text-align:center;font-size:11.5px;color:var(--text-faint);padding:18px}
.crow{position:relative;flex-shrink:0;border:1px solid var(--border);border-left:4px solid var(--pol);border-radius:8px;background:var(--surface-2);padding:5px 8px 6px;cursor:grab}
.crow.pos{--pol:var(--ok,#3aa675)}
.crow.neg{--pol:var(--danger,#e2483d);background:color-mix(in srgb,var(--danger,#e2483d) 7%,var(--surface-2))}
.crow.frozen{--pol:var(--border-strong);background:color-mix(in srgb,var(--surface-3) 55%,transparent);opacity:.72} /* frozen → neutral, not sent to the prompt */
.crow:hover{border-color:var(--border-strong);border-left-color:var(--pol)}
.crow.drop{box-shadow:0 -2px 0 0 var(--accent)}
.crow .cr1{display:flex;align-items:center;gap:6px;min-height:22px}
.crow .grip{color:var(--text-faint);font-size:10px;cursor:grab;flex-shrink:0}
.cdot2{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.cname{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.crsp{margin-left:auto}
.cicon{flex-shrink:0;border:1px solid var(--border-strong);background:var(--surface-1);color:var(--text-dim);border-radius:5px;height:21px;min-width:21px;font-size:11px;font-weight:700;line-height:1;cursor:pointer;padding:0 4px;display:inline-flex;align-items:center;justify-content:center}
.cicon:hover{color:var(--accent);border-color:var(--accent)}
.cicon.pol.pos{color:var(--ok,#3aa675);border-color:color-mix(in srgb,var(--ok,#3aa675) 50%,var(--border))}
.cicon.pol.neg{color:var(--danger,#e2483d);border-color:color-mix(in srgb,var(--danger,#e2483d) 50%,var(--border))}
.cicon.del:hover{color:var(--danger,#e2483d);border-color:var(--danger,#e2483d)}
.cicon.frz.on{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 55%,var(--border));background:var(--nav-active)}
.cprev{font-size:11px;color:var(--text-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
.ctext{width:100%;min-height:52px;margin-top:5px;resize:vertical;font:inherit;font-size:11.5px;color:var(--text);background:var(--surface-1);border:1px solid var(--border);border-radius:5px;padding:6px;outline:none}
.ctext:focus{border-color:var(--accent)}
.comprail{width:118px;flex-shrink:0;border-left:1px solid var(--border);display:flex;flex-direction:column;padding:6px;background:color-mix(in srgb,var(--surface-1) 45%,transparent)}
.railnavs{flex:1;min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:1px}
.clearall{flex-shrink:0;margin-top:5px;border:1px solid var(--border);border-radius:var(--radius);background:transparent;color:var(--text-faint);font:inherit;font-size:10.5px;font-weight:600;padding:5px 6px;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:4px}
.clearall:hover{color:var(--danger,#e2483d);border-color:var(--danger,#e2483d)}
.cnamein{flex:1;min-width:0;font:inherit;font-size:12px;font-weight:600;color:var(--text);background:var(--surface-1);border:1px solid var(--accent);border-radius:4px;padding:1px 5px;outline:none}
.cnav{display:flex;align-items:center;gap:6px;width:100%;border:0;background:transparent;color:var(--text-dim);font:inherit;font-size:11px;font-weight:600;padding:5px 6px;border-radius:var(--radius);cursor:pointer;text-align:left}
.cnav:hover{background:var(--surface-3);color:var(--text)}
.cnav.on{background:var(--nav-active);color:var(--accent)}
.cnav .cdot2,.cnav .gl{width:8px;flex-shrink:0}
.cnav .gl{text-align:center;font-size:11px}
.cnav .cn{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cnav .cc{margin-left:auto;color:var(--text-faint);font-weight:500;font-variant-numeric:tabular-nums;font-size:10px}
.cnav.on .cc{color:var(--accent)}
/* meta footer: assembled +/- prompts + the orientation control on the right */
.stmeta{flex-shrink:0;border-top:1px solid var(--border);background:var(--surface-1);padding:7px 12px;display:flex;align-items:center;gap:10px;font-size:11px;color:var(--text-dim)}
.stmeta .mrows{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px;max-height:52px;overflow-y:auto}
.stmeta .mrow{display:flex;align-items:baseline;gap:8px;white-space:nowrap}
.stmeta .mrow b{color:var(--text-faint);flex-shrink:0}.stmeta .mrow.neg b{color:var(--danger,#e2483d)}
.stmeta .mrow .mtext{overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}
.stmeta .tok{flex-shrink:0;color:var(--text-faint);font-weight:600;font-variant-numeric:tabular-nums}
.stmeta .tok.over{color:var(--danger,#e2483d)}
.orient{flex-shrink:0;display:inline-flex;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.orient button{border:0;border-left:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);font:inherit;font-size:12px;font-weight:600;padding:3px 8px;cursor:pointer}
.orient button:first-child{border-left:0}
.orient button.on{background:var(--nav-active);color:var(--accent)}
.orient button:hover:not(.on){color:var(--text)}

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
