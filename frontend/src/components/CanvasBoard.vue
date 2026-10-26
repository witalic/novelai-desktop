<script setup lang="ts">
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { VueFlow, useVueFlow, type Node as FlowNode } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { ControlButton, Controls } from '@vue-flow/controls'
import { NodeResizer } from '@vue-flow/node-resizer'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/node-resizer/dist/style.css'
import { agentWorkChanges, getVaultConfig, listCategories, resolveBlocks } from '../api'
import { editedWork, onAgentNotice } from '../composables/useAgentNotices'
import { useVault } from '../composables/useVault'
import { applyGalleryOps, applyStarred, planAgentOps, type AgentOp } from '../vault/agentOps'
import { useToast } from '../composables/useToast'
import { useConfirm } from '../composables/useConfirm'
import { useImagePipeline, PICK_SCALES, BASE_LONG, DEFAULT_SPAWN_SCALE } from '../composables/useImagePipeline'
import { useAccount } from '../composables/useAccount'
import { useCatalog, modelSpec } from '../composables/useCatalog'
import { useTokenCount } from '../composables/useTokenCount'
import { useContextMenu, type MenuItem } from '../composables/useContextMenu'
import { useImagePreview } from '../composables/useImagePreview'
import { useImageDownload } from '../composables/useImageDownload'
import { anlasCost } from '../presets/cost'
import { useAutosave } from '../composables/useAutosave'
import { anchorNodes, galleryImageNode, healAnchors, workToCanvas, COMBOS, GALLERY, LIBRARY, STATION } from '../vault/serialize'
import {
  DEFAULT_LAYOUT, MIN_SIZE, boundsOf, fitRect, snapMove, snapResize, type Rect, type Snap, type WidgetKey,
} from '../canvas/widgetGrid'
import {
  blockIdentity, cloneCombos, comboBlocksFromComponents, comboBlocksFromComposition, compDataFromComboBlock, duplicateCombo,
  libraryDrift, lostOnReplace, matchesComposition, newCombo, planAdd, withLatestLibrary, type CompBlockData,
} from '../vault/combos'
import { dedupePrompt } from '../canvas/dedup'
import { imageMeta } from '../canvas/imageMeta'
import { RAIL_MIN, startRailResize } from '../canvas/railResize'
import { buildCategoryTree, groupDot, groupOf, labelOf, nonEmpty, scopeOf } from '../vault/categoryTree'
import PromptWidget from './PromptWidget.vue'
import GalleryStack from './GalleryStack.vue'
import CombosWidget from './CombosWidget.vue'
import ComboDialog from './ComboDialog.vue'
import { removeIdFromGrids } from '../vault/workOps'
import BlockEditorModal from './BlockEditorModal.vue'
import { newId } from '../vault/ids'
import { onBeforeQuit } from '../electron'
import type { NavTarget } from '../App.vue'
import type {
  GenResult, LibraryBlock, PanelParams, PersistedCombo, PersistedComboBlock, PersistedComponent, SnapshotData, WorkDoc,
} from '../types'

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
  navigate: [NavTarget]
  'open-library': [{ category: string; tags: string[] }] // widget footer → Library, pre-filtered
  'new-work': []
  'reload-work': [] // a concurrent edit (409) → the view re-fetches the full work (params + stack + canvas)
  'view-stack': [string] // open the image viewer over the stack at this draft (the view owns the stack)
  'agent-params': [{ workId: string; set: Partial<PanelParams>; presetId?: string }] // an agent set the station's settings (the view owns them)
  'work-gone': [string] // the open work left the vault; the canvas keeps its unsaved changes as a new work (keepAsNew)
}>()

const {
  nodes, addNodes, removeNodes, findNode, onNodeDrag, onNodeDragStop, getIntersectingNodes, viewport,
  screenToFlowCoordinate, setNodes, setViewport, onNodeContextMenu, onSelectionContextMenu, onPaneContextMenu,
} = useVueFlow()

// The "station" is one node: header + two zones (Generation output | Composition block list) + a meta
// footer. The Generation↔Composition ratio, split axis, and lead order live in the station node data.
// STATION/LIBRARY/GALLERY/COMBOS zone ids come from serialize (single source of truth). Content inside them
// is saved; anything loose on the canvas is a draft.
const ANCHORS = new Set([STATION, LIBRARY, GALLERY, COMBOS])

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
const vaultCats = ref<{ slug: string; name: string; color: string }[]>([]) // the whole palette, for the group tree
async function loadCategoryColors() {
  try {
    const cats = await listCategories()
    vaultCatColors.value = Object.fromEntries(cats.map((c) => [c.slug, c.color]))
    vaultCatNames.value = Object.fromEntries(cats.map((c) => [c.slug, c.name]))
    vaultCatOrder.value = cats.map((c) => c.slug)
    vaultCats.value = cats.map(({ slug, name, color }) => ({ slug, name, color }))
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
    // Generation appends quality tags to the positive and prepends the ucPreset undesired-content to the
    // negative (backend augment); both count against their budgets, so the indicator mirrors the web UI.
    quality_toggle: props.params.quality_toggle, uc_preset: props.params.uc_preset,
  }),
)
const posLimit = computed(() => modelSpec(props.params.model)?.token_limit ?? 512)
const negLimit = computed(() => modelSpec(props.params.model)?.negative_token_limit ?? 512)
// Which tokenizer the budget is measured in (v4.5: T5, v5: Qwen) — named in the indicator's tooltip.
const TOKENIZER_NAMES: Record<string, string> = { t5: 'T5', qwen: 'Qwen', clip: 'CLIP' }
const tokenizerName = computed(() => TOKENIZER_NAMES[modelSpec(props.params.model)?.tokenizer ?? 't5'])
// Loose blocks/images not inside any anchor zone are scratch — saved with the work, but with no
// role in generation or galleries.
const scratchCount = computed(() => nodes.value.filter((n) => (n.type === 'block' || n.type === 'image') && !ANCHORS.has(n.parentNode ?? '')).length)

const flowRef = ref<HTMLElement | null>(null)

// Image-rendering pipeline (decode sizing, ?w= thumbnails, flash-free swap, reference scaling). `dims` and
// `sizeOf` are hoisted node-size helpers shared with arrange/settle; the pipeline gets them by reference.
const { shownSrc, scaleOf, imgScale, fullStyle, imgSrc, seedSrc, swapSrc, applyScale } =
  useImagePipeline({ nodes, findNode, sizeOf, dims })

// Per-work quick-access set (Library block ids), persisted on the WorkDoc. Loaded in loadDoc, toggled
// by the widget's ★, and fed to autosave so a star change marks the work dirty. Defined before
// useAutosave so its closure captures an initialised ref.
const favorites = ref<string[]>([])
// The work's saved block combinations (WorkDoc.combos) — owned here, rendered by the Combinations widget.
const combos = ref<PersistedCombo[]>([])

// Vault autosave (dirty flag, flush, save state). Owns the state + logic; the lifecycle (window listeners,
// the periodic timer, and the KeepAlive activate/deactivate hooks) is wired in onMounted/onUnmounted below.
const { title, cover, vaultReady, workId, baseUpdatedAt, saveState, savedAt, markDirty, flush, flushIfDirty, manualSave,
  onBeforeUnload, refreshInterval, stopAutosave, resetBaseline, onAgentEdit, settle, hasUnsaved } = useAutosave({
  nodes, viewport, params: () => props.params, drafts: () => props.drafts, favorites: () => favorites.value,
  combos: () => combos.value,
  onNoVault: () => emit('navigate', 'settings/vaults'), onSaved: rewriteSavedUrls, onConflict: reloadWork,
  onGone: keepAsNew,
  agentChanges: agentWorkChanges, applyAgentOps,
})
// A concurrent edit (409) → the view re-fetches the FULL work (params + draft stack + canvas doc), not
// just the canvas nodes, so the next save doesn't write stale params/stack over the fresh version (H3/#4).
function reloadWork() { emit('reload-work') }
// An agent's edits of this work, replayed over the live canvas (vault/agentOps): its title, station settings,
// combinations and image metadata change; images it generated join the gallery's Quick access; its gallery layout
// and cover land; gallery images it deleted leave their albums. Everything else stays as it is.
function applyAgentOps(ops: AgentOp[]) {
  const edits = planAgentOps(ops, combos.value)
  if (edits.title !== undefined) title.value = edits.title
  if (edits.params) emit('agent-params', { workId: workId.value, set: edits.params, presetId: edits.presetId }) // synchronous: lands in the replay
  if (edits.starred.length) favorites.value = applyStarred(favorites.value, edits.starred)
  combos.value = edits.combos
  for (const { image, snapshot } of edits.added) {
    if (findNode(image.id)) continue
    const { w, h } = spawnSize(image.ar || 3 / 4)
    addNodes([{ ...galleryImageNode(workId.value, image, snapshot), hidden: true, style: { width: `${w}px`, height: `${h}px` } } as FlowNode])
    seedSrc(image.id)
  }
  for (const [id, set] of edits.images) {
    const n = findNode(id)
    if (n?.type === 'image') n.data = { ...n.data, ...set }
  }
  const inGallery = (id: string) => findNode(id)?.parentNode === GALLERY
  const zone = findNode(GALLERY)
  if (zone && edits.gallery.length) applyGalleryOps((zone.data.blocks ??= []), edits.gallery, inGallery)
  if (edits.cover && inGallery(edits.cover)) cover.value = edits.cover
  const gone = edits.deleted.filter((id) => findNode(id))
  for (const id of gone) removeImageFromGridAlbums(id)
  if (gone.length) removeNodes(gone)
}
// What agents change elsewhere: this work → catch up now; the Library → the prompt widget, the category palette
// and the combinations' drift badges re-read it.
// The vault can't be reached: saves wait (the chip and the app's strip say so); once it's back, what waited is saved.
const { unreachable: vaultAway, onBack: onVaultBack } = useVault()
onUnmounted(onVaultBack(() => { flushIfDirty() }))
onAgentNotice((n) => {
  if (editedWork(n, workId.value)) onAgentEdit()
  if (n.kind === 'work_deleted' && n.work_id === workId.value) onWorkDeleted(n.work_id)
  if (n.kind === 'library') { widgetRevalidate.value++; loadCategoryColors() }
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
  if (payload.startsWith('nai-combo:')) { // a Combinations card dropped onto the station → Add (anywhere else: ignored)
    if (overStationAt(toFlow(e.clientX, e.clientY))) applyCombo({ id: payload.slice('nai-combo:'.length), mode: 'add' })
    return
  }
  if (payload.startsWith('nai-libblock:')) { // a widget row dragged out → copy of the vault block
    try {
      const b = JSON.parse(payload.slice('nai-libblock:'.length)) as LibraryBlock
      dropBlockAt(libraryBlockData(b), toFlow(e.clientX, e.clientY))
    } catch { /* malformed payload — ignore */ }
    return
  }
  if (payload.startsWith('nai-comp:')) { // a composition row dragged out of the station → MOVE it loose onto the canvas
    const src = findNode(payload.slice('nai-comp:'.length))
    const pos = toFlow(e.clientX, e.clientY)
    // Let go anywhere over the station (not only on another row) = it stays in the composition — it used to
    // fall through to here, get detached and land on top of (or, selected, under) the station.
    if (!src || src.type !== 'block' || overStationAt(pos)) return
    src.parentNode = undefined
    src.hidden = false
    src.data = { ...src.data, expanded: false, editing: false }
    src.position = clearOfStation({ x: pos.x - 88, y: pos.y - 17 }, 176, 34)
    src.style = { width: '176px' }
    nudgeIfOverlapping(src)
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
const compFilter = ref('') // single-select category rail: '' = all, a group key, or a subgroup slug
// The composition's categories as two-level groups (vault/categoryTree) — the whole palette with the station's
// counts, so group keys match the Library widget's; the rail shows only what the composition holds.
const compTree = computed(() => {
  const counts: Record<string, number> = {}
  for (const b of compBlocks.value) counts[b.data.category] = (counts[b.data.category] || 0) + 1
  const known = new Set(vaultCats.value.map((c) => c.slug))
  const strays = Object.keys(counts).filter((s) => !known.has(s)) // a category the vault no longer has
  return buildCategoryTree([...vaultCats.value, ...strays.map((s) => ({ slug: s, name: catName(s), color: catColor(s) }))]
    .map((c) => ({ ...c, count: counts[c.slug] || 0 })))
})
const compScope = computed(() => scopeOf(compTree.value, compFilter.value))
const compRows = computed(() => {
  let rows = compBlocks.value.filter((n) => !compScope.value.length || compScope.value.includes(n.data.category))
  // Across several categories (All, or a whole group) group by the vault category order (then manual
  // position.y within a category); one category keeps pure position.y. Drag-reorder works in both.
  if (compScope.value.length !== 1) {
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
// The rail: only the groups/subgroups present in the composition (plus the selection), vault order.
const compRail = computed(() => nonEmpty(compTree.value, compFilter.value))
// Open groups: toggled by their chevron; selecting into a group opens it (and it can still be closed after).
const compOpened = ref(new Set<string>())
const compIsOpen = (key: string) => compOpened.value.has(key)
function toggleCompGroup(key: string) {
  const next = new Set(compOpened.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  compOpened.value = next
}
watch(compFilter, (key) => {
  const g = groupOf(compTree.value, key)
  if (g?.children.length && !compOpened.value.has(g.key)) compOpened.value = new Set([...compOpened.value, g.key])
})
// "＋ New block" lands in the rail's selection — a group's own category, a subgroup, else Custom.
const compNewCategory = computed(() => {
  const g = compTree.value.find((x) => x.key === compFilter.value)
  return g ? g.self?.slug ?? 'custom' : compFilter.value || 'custom'
})
function onStationNewBlock() {
  const cat = compNewCategory.value
  newCompBlock(cat)
  if (compScope.value.length && !compScope.value.includes(cat)) compFilter.value = '' // keep the new row in view
}
const compSelName = computed(() => labelOf(compTree.value, compFilter.value, catName))
const compRailEl = ref<HTMLElement | null>(null)
function onCompRailResize(e: PointerEvent) {
  const st = findNode(STATION)
  if (st && compRailEl.value) startRailResize(e, compRailEl.value, 'left', (w) => { st.data.railW = w })
}
function resetCompRail() { const st = findNode(STATION); if (st) delete st.data.railW }

// ---- the colour-dot group picker: move a composition row into another group ----
const catPicker = ref<{ nodeId: string; x: number; y: number } | null>(null)
const pickerTree = computed(() => buildCategoryTree(vaultCats.value.map((c) => ({ ...c, count: 0 })))) // every group, empty or not
function openCatPicker(e: MouseEvent, nodeId: string) {
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
  catPicker.value = { nodeId, x: Math.min(r.left, window.innerWidth - 250), y: Math.min(r.bottom + 6, window.innerHeight - 380) }
}
function pickCategory(slug: string) {
  const p = catPicker.value
  catPicker.value = null
  if (p) patchCompBlock({ nodeId: p.nodeId, patch: { category: slug } })
}
const pickedCategory = computed(() => (catPicker.value ? findNode(catPicker.value.nodeId)?.data?.category : null))

function hideStationBlocks() {
  for (const n of nodes.value) if (n.parentNode === STATION && n.type === 'block') n.hidden = true
}
// Gallery images render only inside the structured gallery widget's grids — the nodes stay (their
// domain is WorkDoc.images) but are hidden on the canvas, like station composition blocks.
function hideGalleryImages() {
  for (const n of nodes.value) if (n.parentNode === GALLERY && n.type === 'image') n.hidden = true
}
// The gallery's image nodes, oldest→newest, handed to the GalleryStack for its grids.
const galleryImages = computed(() =>
  nodes.value.filter((n) => n.type === 'image' && n.parentNode === GALLERY)
    .sort((a, b) => String(a.data?.created_at || '').localeCompare(String(b.data?.created_at || ''))),
)
// The cover the work saves with (canvasToWork): the chosen one while it is a gallery image, else the first.
const shownCover = computed(() => {
  const inGallery = nodes.value.filter((n) => n.type === 'image' && n.parentNode === GALLERY)
  return (inGallery.find((n) => n.id === cover.value) ?? inGallery[0])?.id ?? null
})
// A chosen cover that leaves the gallery (deleted, moved out onto the canvas) is no longer the choice: brought back,
// it doesn't take over again from the first gallery image.
watch(() => !!cover.value && !nodes.value.some((n) => n.id === cover.value && n.type === 'image' && n.parentNode === GALLERY),
  (gone) => { if (gone) cover.value = null })
function toggleImageFavorite(id: string) {
  const n = findNode(id)
  if (n && n.type === 'image') n.data.favorite = !n.data.favorite // mutation → tracked + autosaved
}
// ---- gallery grid albums: each gallery image belongs to exactly one grid (data.blocks) ----
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function galleryGrids(): any[] {
  return (((findNode(GALLERY)?.data.blocks as any[]) || []).filter((b) => b.type === 'grid'))
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function removeImageFromGridAlbums(id: string) { removeIdFromGrids(findNode(GALLERY)?.data?.blocks as any, id) } // shared invariant (workOps)
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
  setComboLink(undefined) // an empty station holds no combination
}

// Auto-size an expanded prompt textarea to fit its content (grows on open + while typing).
function autosize(el: HTMLTextAreaElement) { el.style.height = 'auto'; el.style.height = `${Math.max(52, el.scrollHeight)}px` }
const vAutosize = { mounted: (el: HTMLTextAreaElement) => autosize(el) }

// Interacting with a composition row/rail must not select/drag the whole station node — stop the
// press from reaching Vue Flow, but only when it started on an interactive element (rows/buttons/inputs).
function stopIfInteractive(e: Event) {
  const t = e.target as HTMLElement | null
  if (t && t !== e.currentTarget && t.closest('button, input, textarea, .crow, .cnav, .clearall, .railsave')) e.stopPropagation()
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

// ---- widgets on their 40px module grid (canvas/widgetGrid, design/widget-grid.html) ----
// The four widgets — Prompt blocks, Combinations, Gallery zones and the station — vs everything loose.
const WIDGET_KEY: Record<string, WidgetKey> = { [LIBRARY]: 'library', [COMBOS]: 'combos', [STATION]: 'station', [GALLERY]: 'gallery' }
const layoutStyle = (r: Rect) => ({ width: `${r.w}px`, height: `${r.h}px` })
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const rectOf = (n: any): Rect => { const { w, h } = dims(n); return { x: n.position.x, y: n.position.y, w, h } }
const widgetNodes = () => nodes.value.filter((n) => n.id in WIDGET_KEY)

// The anchor zones only — the skeleton every work starts from, in the default layout (serialize anchorNodes).
const zoneNodes = () => anchorNodes() as unknown as FlowNode[]
// Frame the widgets in whatever window the canvas has (not a fixed zoom).
function fitWidgets() {
  const el = flowRef.value
  const rects = widgetNodes().map(rectOf)
  if (el && rects.length) setViewport(fitRect(boundsOf(rects), { w: el.clientWidth, h: el.clientHeight }))
}
// ▦ Arrange widgets: back to the default positions and sizes; content and loose nodes stay as they are.
function arrangeWidgets() {
  for (const n of widgetNodes()) {
    const r = DEFAULT_LAYOUT[WIDGET_KEY[n.id]]
    n.position = { x: r.x, y: r.y }
    n.style = { ...(n.style as object), ...layoutStyle(r) }
  }
  nextTick(fitWidgets)
}

// Dragging or resizing a widget: a dashed outline + guides show where it will land (snapped to the module
// grid and pulled to neighbours); letting go settles it there. Loose images/blocks move freely.
const widgetSnap = ref<Snap | null>(null)
const SNAP_PX = 14 // pull distance in screen px — converted by the zoom
const snapTol = () => SNAP_PX / (viewport.value.zoom || 1)
const othersOf = (id: string) => widgetNodes().filter((n) => n.id !== id).map(rectOf)
onNodeDrag(({ node, nodes: dragged }) => {
  widgetSnap.value = node.id in WIDGET_KEY && (!dragged || dragged.length <= 1)
    ? snapMove(rectOf(node), othersOf(node.id), snapTol()) : null
})
let resizeStart: Rect | null = null // the widget's box when the resize began — which edges move is judged against it
function onWidgetResizeStart(p: { x: number; y: number; width: number; height: number }) {
  resizeStart = { x: p.x, y: p.y, w: p.width, h: p.height }
}
function onWidgetResize(id: string, p: { x: number; y: number; width: number; height: number }) {
  if (!resizeStart) return
  widgetSnap.value = snapResize({ x: p.x, y: p.y, w: p.width, h: p.height }, resizeStart, othersOf(id), snapTol(), MIN_SIZE[WIDGET_KEY[id]])
}
function onWidgetResizeEnd(id: string) {
  const snap = widgetSnap.value
  widgetSnap.value = null
  resizeStart = null
  const n = findNode(id)
  if (!n || !snap) return
  nextTick(() => { // after the resizer's own last update
    n.position = { x: snap.rect.x, y: snap.rect.y }
    n.style = { ...(n.style as object), ...layoutStyle(snap.rect) }
  })
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
  startFresh()
}
function startFresh() {
  workId.value = newId('work')
  baseUpdatedAt.value = '' // a fresh work has no stored copy → no optimistic-lock base
  title.value = ''
  cover.value = null
  combos.value = []
  favorites.value = []
  setNodes(zoneNodes())
  shownSrc.value = {} // no images in a fresh work — drop the previous work's entries
  nextTick(fitWidgets)
  resetBaseline('idle')
  emit('new-work') // GenerateView drops the stack and re-seeds params from the default preset
}

// The open work was deleted (by an agent, or from the Works list). Once a running save has its answer: with nothing
// unsaved, a new work takes its place; with unsaved changes, they stay (keepAsNew).
async function onWorkDeleted(id: string) {
  await settle()
  if (workId.value !== id) return // that save found it gone and kept the changes already
  if (hasUnsaved()) { keepAsNew(id); markDirty() } else startFresh()
}
// The open work is gone from the vault — deleted, or the vault changed under it — while the canvas holds changes it
// never saved. They stay, as a new work: only what lived in that work's folder (its saved images, its saved stack)
// went with it.
function keepAsNew(oldId: string) {
  const inFolder = (url: unknown) => String(url || '').startsWith(`/api/vault/works/${oldId}/`)
  const lost = nodes.value.filter((n) => n.type === 'image' && inFolder(n.data?.url)).map((n) => n.id)
  for (const id of lost) removeImageFromGridAlbums(id)
  if (lost.length) removeNodes(lost)
  workId.value = newId('work')
  baseUpdatedAt.value = '' // a new work: nothing stored to be newer than, nothing to be "gone" again
  cover.value = null
  emit('work-gone', oldId) // GenerateView drops the stack's saved drafts (their files went with the folder)
  toast.push('This work is no longer in the vault — your unsaved changes stay here as a new work.', 'err', 8000)
}

onMounted(async () => {
  await checkVault()
  if (props.openWork) loadDoc(props.openWork)
  else { addNodes(zoneNodes()); nextTick(fitWidgets) } // fresh start → the default layout, fitted to the window

})
watch(() => props.openWork, (w) => { if (w) loadDoc(w) })

// Settle EVERY dragged node (multi-select moves a whole set) — not just the grabbed one.
onNodeDragStop(({ nodes: dragged, node, event }) => {
  const snap = widgetSnap.value
  widgetSnap.value = null
  if (snap && node.id in WIDGET_KEY) { // a widget settles where its outline showed
    const live = findNode(node.id)
    if (live) live.position = { x: snap.rect.x, y: snap.rect.y }
    return
  }
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
// Is the drop pointer actually over the gallery widget (not just the dragged node's box grazing the zone
// edge)? Requiring this stops an image that merely clipped the corner from vanishing into Quick access (M9).
function pointerOverGallery(p: { x: number; y: number } | undefined): boolean {
  return !!p && document.elementsFromPoint(p.x, p.y).some((el) => !!(el as HTMLElement).closest?.('.gnode'))
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
    // Single drag → require the pointer to actually be over the widget (M9); a multi-drag (no single
    // pointer) falls back to box intersection so a group still lands in the gallery.
    if (gal && (!dropXY || pointerOverGallery(dropXY))) {
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

// ---- copy semantics: every block that enters the work is an independent copy ----
// A block already contributes to the prompt if the composition holds one with the same polarity +
// text (its identity for generation). Copying an identical one adds nothing, so we warn instead of
// duplicating — regardless of the copy method (＋, ⇢, or drag into the station).
function stationHasBlock(polarity: string, text: string): boolean {
  const key = blockIdentity({ polarity, text })
  return nodes.value.some((n) => n.type === 'block' && n.parentNode === STATION && blockIdentity(n.data ?? {}) === key)
}
function warnDuplicate() {
  toast.push('That block is already in the generation area — not duplicated', 'err')
}
// Identities (polarity + text) currently in the composition — the Library widget greys these out.
const usedBlockKeys = computed(() => new Set(compBlocks.value.map((n) => blockIdentity(n.data ?? {}))))

// Is a flow-space point inside the station node?
function overStationAt(pos: { x: number; y: number }): boolean {
  const st = findNode(STATION)
  if (!st) return false
  const stPos = st.computedPosition || st.position
  const { w, h } = dims(st)
  return pos.x >= stPos.x && pos.x <= stPos.x + w && pos.y >= stPos.y && pos.y <= stPos.y + h
}

// A loose block's top-left that keeps its whole box off the station: dropped just past an edge, it would
// otherwise cover the station's edge. Pushed out the short way.
function clearOfStation(p: { x: number; y: number }, w: number, h: number): { x: number; y: number } {
  const st = findNode(STATION)
  if (!st) return p
  const sp = st.computedPosition || st.position
  const { w: sw, h: sh } = dims(st)
  const gap = 12
  const overlaps = p.x < sp.x + sw && p.x + w > sp.x && p.y < sp.y + sh && p.y + h > sp.y
  if (!overlaps) return p
  const moves = [
    { d: sp.x - gap - w - p.x, at: { x: sp.x - gap - w, y: p.y } }, // out the left side
    { d: sp.x + sw + gap - p.x, at: { x: sp.x + sw + gap, y: p.y } }, // the right
    { d: sp.y - gap - h - p.y, at: { x: p.x, y: sp.y - gap - h } }, // the top
    { d: sp.y + sh + gap - p.y, at: { x: p.x, y: sp.y + sh + gap } }, // the bottom
  ]
  return moves.reduce((best, m) => (Math.abs(m.d) < Math.abs(best.d) ? m : best)).at
}

// Drop from the Library widget (drag-out): an independent copy joins the composition when dropped
// over the station, else lands loose on the canvas as scratch.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function dropBlockAt(data: any, pos: { x: number; y: number }) {
  if (overStationAt(pos)) {
    appendToComp(data)
    return
  }
  const bw = 176, bh = 34
  addNodes([{
    id: newId('blk'), type: 'block', zIndex: 2,
    position: clearOfStation({ x: pos.x - bw / 2, y: pos.y - bh / 2 }, bw, bh), style: { width: `${bw}px` },
    data,
  }])
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function libraryBlockData(b: LibraryBlock): any {
  return { category: b.category, name: b.name, text: b.text, polarity: b.polarity,
    block_id: b.id, version: b.version ?? 1, tags: [...(b.tags ?? [])], expanded: false }
}

// ⇢ / drop / ＋: an independent copy appended to the END of the composition list (position.y order).
// Polarity is the block's own (no lanes); duplicates (same polarity + text) are refused, not stacked —
// an empty block is a placeholder still to be written, never a duplicate. Returns the new row's id.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function appendToComp(data: any, expanded = false): string | null {
  if (!findNode(STATION)) return null
  if (String(data.text || '').trim() && stationHasBlock(data.polarity ?? 'positive', data.text)) { warnDuplicate(); return null }
  const id = newId('blk')
  addNodes([{
    id, type: 'block', parentNode: STATION, zIndex: 2, hidden: true,
    position: { x: 0, y: nextCompY() }, style: { width: '176px' },
    data: { ...data, expanded },
  }])
  return id
}
function useLibraryBlock(b: LibraryBlock) {
  appendToComp(libraryBlockData(b))
}

// ＋ New block (the station's composition header, or the Library widget): a fresh work-local block appended to
// the composition, opened for typing.
function newCompBlock(category: string) {
  const id = appendToComp({ category: category || 'custom', name: 'Untitled', text: '', polarity: 'positive', tags: [] }, true)
  if (id) focusWhenRendered(`ctext-${id}`)
}
// Vue Flow renders added nodes a little later — wait for the row's field before focusing it (timers, not
// animation frames: those don't run while the window isn't painting).
function focusWhenRendered(elementId: string, tries = 10) {
  const el = document.getElementById(elementId)
  if (el) el.focus()
  else if (tries > 0) setTimeout(() => focusWhenRendered(elementId, tries - 1), 16)
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

/* ============================ Combinations (saved block sets) ============================ */
// A combination is an ordered set of composition blocks saved in the work (blocks only — see vault/combos).
// The station links to the one it was restored from / saved as (`station.data.comboId`, persisted layout);
// the header names it and shows whether the composition still matches. Add / Clear all drop the link.
const compData = computed(() => compBlocks.value.map((n) => n.data as CompBlockData)) // station order (position.y)
const linkedComboId = computed(() => (nodes.value.find((n) => n.id === STATION)?.data?.comboId as string | undefined) || undefined)
const linkedCombo = computed(() => combos.value.find((c) => c.id === linkedComboId.value) ?? null)
const comboLinkState = computed(() => (linkedCombo.value
  ? (matchesComposition(linkedCombo.value, compData.value) ? 'saved' : 'edited')
  : null))
function setComboLink(id: string | undefined) {
  const st = findNode(STATION)
  if (st) st.data.comboId = id
}
const findCombo = (id: string) => combos.value.find((c) => c.id === id)
const touch = (c: PersistedCombo) => { c.origin = 'user'; c.updated_at = new Date().toISOString() } // a user edit un-badges an agent's set

// A starting name from the blocks: the character, outfit and pose names, else "Combination N" — never one
// already taken in this work ("… (2)").
function suggestComboName(blocks: PersistedComboBlock[]): string {
  const pick = (cat: string) => blocks.find((b) => b.category === cat && !b.frozen)?.name
  const parts = [pick('character'), pick('outfit'), pick('pose')].filter(Boolean)
  const taken = new Set(combos.value.map((c) => c.name))
  if (parts.length) {
    let name = parts.join(' · ')
    for (let n = 2; taken.has(name); n++) name = `${parts.join(' · ')} (${n})`
    return name
  }
  let n = combos.value.length + 1
  while (taken.has(`Combination ${n}`)) n++
  return `Combination ${n}`
}

// Station rows for a combination's blocks: replace = all of them in order; add = only the new ones, after.
function addCompRows(blocks: PersistedComboBlock[], startY: number) {
  addNodes(blocks.map((b, i) => ({
    id: newId('blk'), type: 'block', parentNode: STATION, zIndex: 2, hidden: true,
    position: { x: 0, y: startY + i * 10 }, style: { width: '176px' }, data: compDataFromComboBlock(b),
  })))
}
function replaceComposition(blocks: PersistedComboBlock[], linkId: string | undefined) {
  removeNodes(compBlocks.value.map((n) => n.id))
  addCompRows(blocks, 0) // exactly what was saved, so a restored set reads "saved", never "edited"
  setComboLink(linkId)
}

// ---- dialogs (save / replace) ----
type ComboDialogState =
  | { mode: 'save'; title: string; name: string; blocks: PersistedComboBlock[]; link: boolean }
  | { mode: 'replace'; title: string; message: string; blocks: PersistedComboBlock[]; linkId?: string }
const comboDialog = ref<ComboDialogState | null>(null)

function openSaveCurrent() {
  const blocks = comboBlocksFromComposition(compData.value)
  if (!blocks.length) { toast.push('Add blocks to the composition first', 'err'); return }
  comboDialog.value = { mode: 'save', title: 'Save combination', name: suggestComboName(blocks), blocks, link: true }
}
function onComboDialogSave(p: { name: string; keepFrozen: boolean }) {
  const d = comboDialog.value
  comboDialog.value = null
  if (!d || d.mode !== 'save') return
  const dropped = !p.keepFrozen && d.blocks.some((b) => b.frozen)
  const c = newCombo(p.name, dropped ? d.blocks.filter((b) => !b.frozen) : d.blocks)
  combos.value.unshift(c) // newest first — visible at the top of the widget
  if (d.link && !dropped) setComboLink(c.id) // without its frozen rows the set isn't what the station holds
  toast.push(`Saved combination “${c.name}”`, 'ok')
}
// Replace asks only when it would really lose something: station blocks that aren't in the incoming set AND
// aren't already kept as the (unchanged) linked combination.
function askReplace(blocks: PersistedComboBlock[], what: string, linkId?: string) {
  const lost = lostOnReplace(compData.value, blocks)
  if (!lost || comboLinkState.value === 'saved') { replaceComposition(blocks, linkId); return }
  comboDialog.value = {
    mode: 'replace', title: 'Replace the composition?', blocks, linkId,
    message: `The station has ${lost} block${lost === 1 ? '' : 's'} that ${lost === 1 ? 'is' : 'are'} not in ${what}. ${lost === 1 ? 'It' : 'They'} will be removed.`,
  }
}
function onComboDialogReplace(p: { saveFirst: boolean }) {
  const d = comboDialog.value
  comboDialog.value = null
  if (!d || d.mode !== 'replace') return
  if (p.saveFirst) {
    const current = comboBlocksFromComposition(compData.value)
    const name = linkedCombo.value && comboLinkState.value === 'edited' ? `${linkedCombo.value.name} (edited)` : suggestComboName(current)
    combos.value.unshift(newCombo(name, current))
  }
  replaceComposition(d.blocks, d.linkId)
}

// ---- widget / station actions ----
function applyCombo(p: { id: string; mode: 'add' | 'replace' }) {
  const c = findCombo(p.id)
  if (!c) return
  if (p.mode === 'replace') { askReplace(c.blocks, `“${c.name}”`, c.id); return }
  const { toAdd, skipped } = planAdd(c.blocks, compData.value)
  if (toAdd.length) {
    addCompRows(toAdd, nextCompY())
    setComboLink(undefined) // appended onto other blocks — no longer exactly this (or any) combination
  }
  toast.push(toAdd.length
    ? `Added ${toAdd.length} block${toAdd.length === 1 ? '' : 's'} from “${c.name}”${skipped ? ` · ${skipped} already there` : ''}`
    : `Every block of “${c.name}” is already in the composition`, toAdd.length ? 'ok' : 'err')
}
async function updateCombo(id: string) {
  const c = findCombo(id)
  if (!c) return
  const blocks = comboBlocksFromComposition(compData.value)
  if (!blocks.length) { toast.push('The composition is empty — nothing to save', 'err'); return }
  // Overwriting the set the station came from is the expected flow; any other set is a real loss — ask, like Delete.
  if (id !== linkedComboId.value && !(await confirm({
    title: 'Update combination', confirmLabel: 'Overwrite',
    message: `Overwrite “${c.name}” (${c.blocks.length} block${c.blocks.length === 1 ? '' : 's'}) with the station’s ${blocks.length} block${blocks.length === 1 ? '' : 's'}?`,
  }))) return
  c.blocks = blocks
  touch(c)
  setComboLink(c.id)
  toast.push(`Updated “${c.name}”`, 'ok')
}
function renameCombo(p: { id: string; name: string }) {
  const c = findCombo(p.id)
  if (c) { c.name = p.name; touch(c) }
}
function duplicateComboById(id: string) {
  const i = combos.value.findIndex((c) => c.id === id)
  if (i >= 0) combos.value.splice(i + 1, 0, duplicateCombo(combos.value[i]))
}
async function deleteCombo(id: string) {
  const c = findCombo(id)
  if (!c || !(await confirm({
    title: 'Delete combination', danger: true, confirmLabel: 'Delete',
    message: `Delete “${c.name}”? The blocks in the station stay; only the saved set is removed.`,
  }))) return
  combos.value = combos.value.filter((x) => x.id !== id)
  if (linkedComboId.value === id) setComboLink(undefined)
}
async function useLatestVersions(id: string) {
  const c = findCombo(id)
  const ids = [...new Set((c?.blocks ?? []).map((b) => b.block_id).filter((x): x is string => !!x))]
  if (!c || !ids.length) return
  try {
    const latest = await resolveBlocks(ids)
    c.blocks = withLatestLibrary(c.blocks, new Map(latest.map((b) => [b.id, b])))
    touch(c)
    toast.push(`“${c.name}” now uses the latest Library versions`, 'ok')
  } catch (e) {
    toast.push(e instanceof Error ? e.message : 'Could not read the Library', 'err')
  }
}

// Library drift per combination (blocks edited in the Library after the set saved them) — card badges.
const comboDrift = ref<Record<string, number>>({})
let driftTimer: ReturnType<typeof setTimeout> | null = null
function refreshComboDrift() {
  if (driftTimer) clearTimeout(driftTimer)
  driftTimer = setTimeout(async () => {
    const ids = [...new Set(combos.value.flatMap((c) => c.blocks.map((b) => b.block_id)).filter((x): x is string => !!x))]
    if (!ids.length) { comboDrift.value = {}; return }
    try {
      const versions = new Map((await resolveBlocks(ids)).map((b) => [b.id, b.version ?? 1]))
      comboDrift.value = Object.fromEntries(combos.value.map((c) => [c.id, libraryDrift(c, versions)]))
    } catch { /* no vault / backend not ready — keep the last known badges */ }
  }, 300)
}
watch(combos, refreshComboDrift, { deep: true })
watch(widgetRevalidate, refreshComboDrift) // Library edits elsewhere (returning to Generate, opening a work)

// ---- gallery image: its recipe → the station or a new combination ----
function imageComponents(id: string): PersistedComponent[] {
  const snap = findNode(id)?.data?.snapshot as SnapshotData | undefined
  return snap?.components ?? []
}
function onGalleryImageMenu(p: { event: MouseEvent; id: string }) {
  const n = findNode(p.id)
  if (!n || n.type !== 'image') return
  const recipe = comboBlocksFromComponents(imageComponents(p.id))
  const items: MenuItem[] = [
    { label: 'Preview', icon: '⤢', onClick: () => viewGallery(p.id) },
    { label: n.data.favorite ? 'Unfavorite' : 'Favorite', icon: '★', onClick: () => toggleImageFavorite(p.id) },
  ]
  if (shownCover.value !== p.id) items.push({ label: 'Set as cover', icon: '▣', onClick: () => { cover.value = p.id } })
  if (recipe.length) {
    items.push({ label: 'Restore recipe to the station', icon: '↺', onClick: () => restoreRecipe(p.id) })
    items.push({ label: 'Save recipe as combination…', icon: '◈', onClick: () => saveRecipe(p.id) })
  }
  items.push({ label: 'Delete', icon: '🗑', danger: true, onClick: () => onGalleryDeleteImg(p.id) })
  openMenu(p.event, items)
}
function restoreRecipe(id: string) {
  const recipe = comboBlocksFromComponents(imageComponents(id))
  if (recipe.length) askReplace(recipe, 'this image’s recipe')
  else toast.push('This image has no recipe to restore', 'err')
}
function saveRecipe(id: string) {
  const recipe = comboBlocksFromComponents(imageComponents(id))
  if (!recipe.length) { toast.push('This image has no recipe to save', 'err'); return }
  comboDialog.value = { mode: 'save', title: 'Save recipe as combination', name: suggestComboName(recipe), blocks: recipe, link: false }
}

// ---- the image viewer over a gallery set: one grid (in order) or Quick access ----
// The set an image belongs to — its grid's ids ("Grid N", as the Outline names it), or Quick access (gallery
// images in no grid), oldest first.
function gallerySetOf(id: string): { source: string; ids: () => string[] } {
  const grids = galleryGrids()
  const at = grids.findIndex((g) => g.imageIds.includes(id))
  if (at >= 0) {
    const gridId = grids[at].id
    return { source: `Grid ${at + 1}`, ids: () => (galleryGrids().find((g) => g.id === gridId)?.imageIds ?? []) as string[] }
  }
  return {
    source: 'Quick access',
    ids: () => {
      const inGrids = new Set(galleryGrids().flatMap((g) => g.imageIds as string[]))
      return galleryImages.value.filter((n) => !inGrids.has(n.id)).map((n) => n.id)
    },
  }
}
function viewGallery(startId: string) {
  const { source, ids } = gallerySetOf(startId)
  openViewer({
    source,
    items: () => ids().map((id) => findNode(id)).filter((n) => n?.type === 'image' && n.data.url).map((n) => {
      const params = (n!.data.snapshot as SnapshotData | undefined)?.params as { width?: number; height?: number; seed?: number } | undefined
      return { id: n!.id, url: n!.data.url as string, favorite: !!n!.data.favorite, cover: n!.id === shownCover.value,
        meta: imageMeta(params, n!.data.created_at) }
    }),
    actions: [
      { label: (it) => (it.favorite ? 'Unfavorite' : 'Favorite'), icon: '★', key: 'f', run: (it) => toggleImageFavorite(it.id) },
      { label: 'Set as cover', icon: '▣', enabled: (it) => !it.cover, run: (it) => { cover.value = it.id } },
      { label: 'Restore recipe', icon: '↺', closes: true, enabled: (it) => imageComponents(it.id).length > 0, run: (it) => restoreRecipe(it.id) },
      { label: 'Save recipe as combination…', icon: '◈', closes: true, enabled: (it) => imageComponents(it.id).length > 0,
        run: (it) => saveRecipe(it.id) },
      { label: 'Download', icon: '⤓', run: (it) => downloadUrls([it.url]) },
      { label: 'Delete', icon: '🗑', key: 'Delete', danger: true, run: (it) => onGalleryDeleteImg(it.id) },
    ],
  }, startId)
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
  if (e.key === 'Escape' && catPicker.value) { catPicker.value = null; return }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); manualSave(); return }
  if (viewerOpen()) return // the image viewer (and any dialog over it) owns the keys — never delete canvas nodes under it
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

// Mark the work dirty on any change to the canvas / params / drafts / title / combinations (see useAutosave).
watch([nodes, () => props.params, () => props.drafts, title, cover, combos], markDirty, { deep: true })

// Re-read whether a vault is configured. Under KeepAlive onMounted runs once, so a vault added in Settings
// mid-session would otherwise leave `vaultReady` false forever → autosave silently dead (H7).
async function checkVault() {
  try { vaultReady.value = !!(await getVaultConfig()).active } catch { /* backend not ready */ }
}

// Lifecycle. The window/quit listeners must be live whenever the app is open, so they sit in onMounted. The
// keydown handler (Ctrl+S / Delete) is scoped to onActivated/onDeactivated: CanvasBoard stays mounted under
// KeepAlive, so a global Delete would otherwise remove canvas nodes while another view is showing (H8).
let disposeBeforeQuit: (() => void) | null = null
// Closing the app: save first. What can't be saved (the vault can't be reached, a failed save) is never thrown away
// unasked — the user decides, and the window stays unless they choose to lose it.
async function quitting(hold: () => void): Promise<boolean> {
  if ((await flush()) || !hasUnsaved()) return true
  hold()
  return confirm({
    title: 'Quit without saving?', danger: true, confirmLabel: 'Quit and lose them',
    message: vaultAway.value
      ? "The vault can't be reached, so your latest changes aren't saved. Quit anyway and lose them? (Keep the app open and they're saved once the vault is back.)"
      : "Your latest changes couldn't be saved. Quit anyway and lose them?",
  })
}
onMounted(() => {
  window.addEventListener('beforeunload', onBeforeUnload)
  disposeBeforeQuit = onBeforeQuit(quitting) // Electron waits for the full save before quitting
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
  // A work made outside the canvas (an agent's) has no layout yet, or just its gallery zone → the default widgets,
  // fitted to the window.
  const unlaid = healAnchors(ns)
  setNodes(ns as unknown as FlowNode[]) // persisted nodes are plain data; Vue Flow hydrates the runtime fields
  shownSrc.value = {} // drop the previous work's entries, then seed this work's images
  for (const n of ns) if (n.type === 'image') seedSrc(n.id)
  favorites.value = [...(doc.favorites || [])] // per-work quick-access set for the widget's ★ filter
  combos.value = cloneCombos(doc.combos || []) // own copy — the widget edits it in place; the doc prop stays as loaded
  hideStationBlocks() // composition blocks render as the station's list, never free on the canvas
  hideGalleryImages() // gallery images render inside the structured gallery widget, not free on the canvas
  widgetRevalidate.value++ // a freshly opened work re-reads the Library (categories/counts)
  if (unlaid) nextTick(fitWidgets)
  else if (vp) setViewport(vp)
  workId.value = doc.id
  baseUpdatedAt.value = doc.updated_at || '' // optimistic-lock base for the next save (H3)
  title.value = doc.title || ''
  cover.value = doc.preview_image_id ?? null
  resetBaseline('saved') // the loaded state is the baseline — not dirty
}

// Right-click menu for images + prompt blocks (shared ContextMenu): preview, arrange, download, delete.
const { open: openMenu, close: closeMenu } = useContextMenu()
const { preview: openPreview, openViewer, isOpen: viewerOpen } = useImagePreview() // `preview` is already a prop (the live generation image)
const { downloadUrls } = useImageDownload()
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
  if (urls.length) items.push({ label: `Download${urls.length > 1 ? ` ${urls.length} images` : ' image'}`, icon: '⤓', onClick: () => downloadUrls(urls) })
  // Anchor zones / the station are never removable — filter them from the delete set.
  items.push({ label: `Delete${ids.length > 1 ? ` ${ids.length} items` : ' item'}`, icon: '🗑', danger: true,
    onClick: () => { const del = ids.filter((id) => !ANCHORS.has(id)); if (del.length) removeNodes(del) } })
  openMenu(event, items)
}
// Right-click the station Output image → same actions as a stack thumbnail (top draft).
function onStationMenu(e: MouseEvent) {
  const d = props.drafts[0]
  if (!d) return
  openMenu(e, [
    { label: 'Preview', icon: '⤢', onClick: () => emit('view-stack', d.id) },
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
      <span v-if="vaultAway && saveState === 'dirty'" class="chip warn">● Not saved — vault unavailable</span>
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
      <!-- elevate-nodes-on-select off: a selected zone (+1000 z) would otherwise cover the loose blocks/images on it -->
      <VueFlow :min-zoom="0.2" :max-zoom="2.5" :delete-key-code="null" :only-render-visible-elements="true"
               :multi-selection-key-code="['Control', 'Meta']" :selection-key-code="'Shift'" :elevate-nodes-on-select="false"
               :zoom-on-double-click="false" style="height:100%;width:100%">
        <Background pattern-color="var(--border-strong)" :gap="18" :size="1.2" />
        <Controls position="bottom-left" :show-interactive="false">
          <ControlButton title="Arrange widgets — back to the default layout" @click="arrangeWidgets">▦</ControlButton>
        </Controls>

        <template #node-station="{ data, selected }">
          <NodeResizer :min-width="MIN_SIZE.station.w" :min-height="MIN_SIZE.station.h" :is-visible="selected" color="var(--accent)"
            @resize-start="(e) => onWidgetResizeStart(e.params)" @resize="(e) => onWidgetResize(STATION, e.params)" @resize-end="onWidgetResizeEnd(STATION)" />
          <div class="station">
            <div class="sthd">
              <span class="sttitle">Generation</span>
              <span v-if="linkedCombo" class="combochip" :class="comboLinkState"
                :title="comboLinkState === 'saved' ? 'The composition matches this combination' : 'The blocks changed since this combination was saved or restored'">
                <span>◈</span><span class="nm">{{ linkedCombo.name }}</span>
                <span class="st">{{ comboLinkState === 'saved' ? '✓ saved' : '● edited' }}</span>
              </span>
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
                  <div v-else-if="drafts.length" class="topwrap nodrag" draggable="true"
                    @dragstart="onDraftDragStart" @pointerdown.stop @click.stop="emit('view-stack', drafts[0].id)"
                    @contextmenu.stop="onStationMenu" title="Click to view the stack · drag onto the canvas to keep · right-click for actions">
                    <img class="topimg" :src="drafts[0].url" alt="latest generation" draggable="false" />
                    <span v-if="drafts[0].mock" class="mockbadge" title="Offline placeholder — no NovelAI token set">MOCK</span>
                    <span class="viewhint">⤢ Click to view{{ drafts.length > 1 ? ` all ${drafts.length}` : '' }}</span>
                    <span class="stackbadge">{{ drafts.length }} in stack</span>
                    <span class="draghint">⤴ drag to keep</span>
                    <div class="topacts nodrag">
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
                  <div class="comphd">
                    <span class="zlbl">Composition · {{ compBlocks.length }}</span>
                    <button class="newblk" :title="`Add an empty block to ${catName(compNewCategory)}`"
                      @click.stop="onStationNewBlock" @pointerdown.stop>＋ New block
                      <i :style="{ background: catColor(compNewCategory) }"></i>{{ catName(compNewCategory) }}</button>
                  </div>
                  <div v-if="!compRows.length" class="comphint">
                    {{ compFilter ? `No ${compSelName} blocks.` : 'Drop blocks from the Library widget to build the prompt.' }}
                  </div>
                  <div v-for="b in compRows" :key="b.nodeId" class="crow"
                    :class="[b.frozen ? 'frozen' : (b.polarity === 'negative' ? 'neg' : 'pos'), { drop: compDropBefore === b.nodeId }]"
                    draggable="true" @dragstart="onCompDragStart($event, b.nodeId)" @dragover="onCompDragOver($event, b.nodeId)" @dragend="onCompDragEnd">
                    <div class="cr1">
                      <span class="grip" title="Drag to reorder or out to the canvas">⠿</span>
                      <button class="cdot2 pick" :class="{ act: catPicker?.nodeId === b.nodeId }" :style="{ background: catColor(b.category) }"
                        :title="`${catName(b.category)} — click to move to another group`"
                        @click.stop="openCatPicker($event, b.nodeId)" @pointerdown.stop></button>
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
                    <textarea v-if="b.expanded" :id="`ctext-${b.nodeId}`" v-autosize class="ctext nodrag nowheel" :value="b.text" placeholder="tags…"
                      @pointerdown.stop @click.stop
                      @input="patchCompBlock({ nodeId: b.nodeId, patch: { text: ($event.target as HTMLTextAreaElement).value } }); autosize($event.target as HTMLTextAreaElement)"></textarea>
                    <div v-else class="cprev">{{ b.text || 'empty' }}</div>
                  </div>
                </div>
                <div ref="compRailEl" class="comprail nodrag nowheel" :style="{ width: `${data.railW ?? RAIL_MIN}px` }"
                  @pointerdown="stopIfInteractive" @mousedown="stopIfInteractive" @click="stopIfInteractive">
                  <div class="railgrip" title="Drag to resize · double-click to reset" @pointerdown="onCompRailResize" @dblclick.stop="resetCompRail"></div>
                  <div class="railnavs">
                    <button class="cnav" :class="{ on: !compFilter }" @click.stop="compFilter = ''" @pointerdown.stop>
                      <span class="chv"></span><span class="gl">▦</span><span class="cn">All</span><span class="cc">{{ compBlocks.length }}</span>
                    </button>
                    <template v-for="g in compRail" :key="g.key">
                      <button class="cnav" :class="{ on: compFilter === g.key }" :title="g.children.length ? `${g.name} — the whole group` : g.name"
                        @click.stop="compFilter = g.key" @pointerdown.stop>
                        <span class="chv" :title="compIsOpen(g.key) ? 'Hide subgroups' : 'Show subgroups'"
                          @click.stop="g.children.length && toggleCompGroup(g.key)">{{ g.children.length ? (compIsOpen(g.key) ? '▾' : '▸') : '' }}</span>
                        <span class="cdot2" :style="{ background: groupDot(g) }"></span><span class="cn">{{ g.name }}</span><span class="cc">{{ g.count }}</span>
                      </button>
                      <template v-if="compIsOpen(g.key)">
                        <button v-for="c in g.children" :key="c.slug" class="cnav child" :class="{ on: compFilter === c.slug, own: c.own }"
                          :title="c.own ? `${g.name} only — without its subgroups` : c.name" @click.stop="compFilter = c.slug" @pointerdown.stop>
                          <span class="cdot2" :style="{ background: c.color }"></span><span class="cn">{{ c.short }}</span><span class="cc">{{ c.count }}</span>
                        </button>
                      </template>
                    </template>
                  </div>
                  <template v-if="compBlocks.length">
                    <button v-if="comboLinkState === 'edited'" class="railsave on" :title="`Overwrite “${linkedCombo?.name}” with the current blocks`"
                      @click.stop="updateCombo(linkedComboId!)" @pointerdown.stop>↻ Update combination</button>
                    <button class="railsave" :class="{ on: !linkedCombo }" title="Save the current blocks as a new combination"
                      @click.stop="openSaveCurrent" @pointerdown.stop>◈ {{ linkedCombo ? 'Save as new…' : 'Save as combination' }}</button>
                  </template>
                  <button v-if="compBlocks.length" class="clearall" title="Remove every block from the composition"
                    @click.stop="clearComposition" @pointerdown.stop>🗑 Clear all</button>
                </div>
              </div>
            </div>

            <div class="stmeta nowheel">
              <div class="mrows">
                <div class="mrow"><b>+</b> <span class="mtext">{{ composed.positive || '—' }}</span>
                  <span class="tok" :class="{ over: posTokens > posLimit }" :title="`Positive prompt — ${posTokens} of ${posLimit} ${tokenizerName} tokens`">{{ posTokens }} / {{ posLimit }}</span></div>
                <div class="mrow neg"><b>−</b> <span class="mtext">{{ composed.negative || '—' }}</span>
                  <span class="tok" :class="{ over: negTokens > negLimit }" :title="`Negative prompt — ${negTokens} of ${negLimit} ${tokenizerName} tokens`">{{ negTokens }} / {{ negLimit }}</span></div>
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
            <NodeResizer :min-width="MIN_SIZE.library.w" :min-height="MIN_SIZE.library.h" :is-visible="selected" color="var(--accent)"
              @resize-start="(e) => onWidgetResizeStart(e.params)" @resize="(e) => onWidgetResize(id, e.params)" @resize-end="onWidgetResizeEnd(id)" />
            <PromptWidget :data="data" :selected="selected" :favorites="favorites" :revalidate="widgetRevalidate" :used-keys="usedBlockKeys"
              @open-library="emit('open-library', $event)"
              @open-settings="emit('navigate', 'settings/vaults')"
              @use="useLibraryBlock" @new-block="newCompBlock" @toggle-favorite="toggleFavorite" />
          </template>
          <template v-else-if="data.role === 'combos'">
            <NodeResizer :min-width="MIN_SIZE.combos.w" :min-height="MIN_SIZE.combos.h" :is-visible="selected" color="var(--accent)"
              @resize-start="(e) => onWidgetResizeStart(e.params)" @resize="(e) => onWidgetResize(id, e.params)" @resize-end="onWidgetResizeEnd(id)" />
            <CombosWidget :combos="combos" :selected="selected" :comp-count="compBlocks.length" :drift="comboDrift"
              :linked-id="linkedComboId" :cat-color="catColor" :cat-name="catName"
              @save-current="openSaveCurrent" @apply="applyCombo" @rename="renameCombo" @update="updateCombo"
              @duplicate="duplicateComboById" @use-latest="useLatestVersions" @delete="deleteCombo" />
          </template>
          <template v-else>
            <NodeResizer :min-width="MIN_SIZE.gallery.w" :min-height="MIN_SIZE.gallery.h" :is-visible="selected" color="var(--accent)"
              @resize-start="(e) => onWidgetResizeStart(e.params)" @resize="(e) => onWidgetResize(id, e.params)" @resize-end="onWidgetResizeEnd(id)" />
            <GalleryStack :data="data" :images="galleryImages" :cover="shownCover" :selected="selected" @favorite="toggleImageFavorite" @preview="viewGallery" @to-quick="(id) => onGalleryQuickDrop({ imageId: id })" @delete-img="onGalleryDeleteImg" @clear-quick="onGalleryClearQuick" @download="downloadUrls" @drop-on-grid="onGalleryGridDrop" @drop-on-quick="onGalleryQuickDrop" @image-menu="onGalleryImageMenu" />
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

      <!-- Where a dragged/resized widget will land (module grid + neighbour magnets), drawn in flow coords. -->
      <div v-if="widgetSnap" class="wsnap"
        :style="{ transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.zoom})`, '--px': `${1 / viewport.zoom}px` }">
        <div class="wsnap-ghost" :style="{ left: `${widgetSnap.rect.x}px`, top: `${widgetSnap.rect.y}px`,
          width: `${widgetSnap.rect.w}px`, height: `${widgetSnap.rect.h}px` }"></div>
        <template v-for="(g, i) in widgetSnap.guides" :key="i">
          <div class="wsnap-guide" :class="g.axis" :style="g.axis === 'x'
            ? { left: `${g.at}px`, top: `${g.from}px`, height: `${g.to - g.from}px` }
            : { top: `${g.at}px`, left: `${g.from}px`, width: `${g.to - g.from}px` }"></div>
          <span v-if="g.gap" class="wsnap-gap" :style="g.axis === 'x'
            ? { left: `${g.at}px`, top: `${(g.from + g.to) / 2}px` } : { left: `${(g.from + g.to) / 2}px`, top: `${g.at}px` }">40</span>
        </template>
      </div>

      <!-- Scale picker under an all-images selection: click a reference scale to resize the whole set. -->
      <div v-if="scalePicker" class="scalepick" :style="{ left: scalePicker.x + 'px', top: scalePicker.y + 'px' }">
        <button v-for="s in PICK_SCALES" :key="s" :class="{ on: s === scalePicker.current }" @click="applyScaleAll(s)">×{{ s }}</button>
      </div>
    </div>

    <!-- Save-to-Library opens the shared editor in-place (no tab switch). -->
    <BlockEditorModal v-if="editingDraft" :block="editingDraft.block" :is-new="true"
      @close="editingDraft = null" @saved="onDraftSaved" />
    <!-- Combinations: save a set / confirm a Replace that would drop station blocks. -->
    <ComboDialog v-if="comboDialog" :mode="comboDialog.mode" :title="comboDialog.title"
      :name="comboDialog.mode === 'save' ? comboDialog.name : undefined" :blocks="comboDialog.blocks" :cat-color="catColor"
      :message="comboDialog.mode === 'replace' ? comboDialog.message : undefined"
      @save="onComboDialogSave" @replace="onComboDialogReplace" @close="comboDialog = null" />
    <!-- A composition row's colour dot → move the block into another group (every group, empty or not). -->
    <Teleport to="body">
      <template v-if="catPicker">
        <div class="cpk-back" @click="catPicker = null" @contextmenu.prevent="catPicker = null" @wheel="catPicker = null"></div>
        <div class="cpk" :style="{ left: `${catPicker.x}px`, top: `${catPicker.y}px` }" @keydown.esc="catPicker = null">
          <div class="cpk-hd">Move to group</div>
          <template v-for="g in pickerTree" :key="g.key">
            <div v-if="g.self?.slug === 'custom'" class="cpk-sep"></div>
            <button v-if="g.self" class="cpk-o grp" :class="{ on: pickedCategory === g.self.slug }" @click="pickCategory(g.self.slug)">
              <i :style="{ background: g.self.color }"></i>{{ g.name }}<span v-if="pickedCategory === g.self.slug" class="ck">✓</span>
            </button>
            <div v-else class="cpk-o virt">{{ g.name }}</div>
            <button v-for="c in g.children.filter((x) => !x.own)" :key="c.slug" class="cpk-o child" :class="{ on: pickedCategory === c.slug }"
              @click="pickCategory(c.slug)">
              <i :style="{ background: c.color }"></i>{{ c.short }}<span v-if="pickedCategory === c.slug" class="ck">✓</span>
            </button>
          </template>
        </div>
      </template>
    </Teleport>
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
.chip.warn{color:var(--warn-fg);border-color:color-mix(in srgb,var(--warn) 45%,var(--border));background:color-mix(in srgb,var(--warn) 14%,var(--surface-1))}
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
.flowwrap :deep(.vue-flow__controls-button){color:var(--text-dim);font-size:13px;line-height:1} /* the text-glyph ▦ button */
.flowwrap :deep(.vue-flow__controls-button:hover){color:var(--text)}
/* the widget snap preview — flow coordinates via the viewport transform; lines stay 1 screen px (--px) */
.wsnap{position:absolute;left:0;top:0;transform-origin:0 0;pointer-events:none;z-index:15}
.wsnap-ghost{position:absolute;border:calc(var(--px) * 2) dashed var(--accent);border-radius:12px;
  background:color-mix(in srgb,var(--accent) 7%,transparent)}
.wsnap-guide{position:absolute;background:var(--accent)}
.wsnap-guide.x{width:var(--px)}
.wsnap-guide.y{height:var(--px)}
.wsnap-gap{position:absolute;transform:translate(-50%,-50%);font-size:calc(var(--px) * 10);font-weight:700;
  color:var(--accent);background:var(--bg);padding:0 calc(var(--px) * 3);border-radius:calc(var(--px) * 3)}
.flowwrap :deep(.vue-flow__node.selected){box-shadow:0 0 0 2px var(--accent)}
.flowwrap :deep(.vue-flow__selection){background:color-mix(in srgb,var(--accent) 14%,transparent);border:1px solid var(--accent);border-radius:4px}
.flowwrap :deep(.vue-flow__nodesselection-rect){background:color-mix(in srgb,var(--accent) 10%,transparent);border:1px solid var(--accent);border-radius:4px}

/* station (coupled Output + composition) */
.station{width:100%;height:100%;display:flex;flex-direction:column;border:1.5px solid var(--border-strong);border-radius:12px;overflow:hidden;background:color-mix(in srgb,var(--surface-1) 70%,transparent)}
.sthd{height:44px;flex-shrink:0;display:flex;align-items:center;gap:8px;padding:0 12px;border-bottom:1px solid var(--border);background:var(--surface-1)}
.sttitle{font-weight:600;font-size:13px}
/* the combination the composition is linked to (name + saved/edited), right of the title */
.combochip{display:inline-flex;align-items:center;gap:6px;min-width:0;max-width:320px;font-size:11.5px;font-weight:650;color:var(--accent);
  background:var(--nav-active);border:1px solid color-mix(in srgb,var(--accent) 40%,var(--border));border-radius:20px;padding:2px 10px}
.combochip .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.combochip .st{flex-shrink:0;font-size:10px;font-weight:700;color:var(--ok)}
.combochip.edited{color:var(--warn-fg);background:color-mix(in srgb,var(--warn) 10%,transparent);border-color:color-mix(in srgb,var(--warn) 45%,var(--border))}
.combochip.edited .st{color:var(--warn-fg)}
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
.topwrap{position:absolute;inset:8px;display:flex;align-items:center;justify-content:center;cursor:zoom-in}
.topwrap:active{cursor:grabbing}
.topimg{max-width:100%;max-height:100%;object-fit:contain;border-radius:8px;border:1px solid var(--border);box-shadow:0 2px 10px rgba(0,0,0,.35);transition:box-shadow .12s,border-color .12s}
.topwrap:hover .topimg{border-color:var(--border-strong)}
.mockbadge{position:absolute;top:14px;left:14px;font-size:9px;font-weight:800;letter-spacing:.5px;background:var(--warn,#b65c02);color:#fff;padding:2px 7px;border-radius:10px;pointer-events:none}
/* hover hint: a click opens the viewer over the whole stack */
.viewhint{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:11.5px;font-weight:700;color:#fff;
  background:color-mix(in srgb,#000 58%,transparent);padding:6px 11px;border-radius:8px;opacity:0;transition:opacity .12s;pointer-events:none;white-space:nowrap}
.topwrap:hover .viewhint{opacity:1}
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
.comprail{position:relative;flex-shrink:0;border-left:1px solid var(--border);display:flex;flex-direction:column;padding:6px;background:color-mix(in srgb,var(--surface-1) 45%,transparent)}
/* the rail's resize handle, on its left edge (the rail sits on the right) */
.railgrip{position:absolute;top:0;bottom:0;left:-4px;width:8px;cursor:col-resize;z-index:2}
.railgrip:hover,.railgrip:active{background:linear-gradient(to right,transparent 2px,var(--accent) 2px,var(--accent) 5px,transparent 5px)}
.railnavs{flex:1;min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:1px}
.clearall{flex-shrink:0;margin-top:5px;border:1px solid var(--border);border-radius:var(--radius);background:transparent;color:var(--text-faint);font:inherit;font-size:10.5px;font-weight:600;padding:5px 6px;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:4px}
.clearall:hover{color:var(--danger,#e2483d);border-color:var(--danger,#e2483d)}
.railsave{flex-shrink:0;margin-top:5px;border:1px solid var(--border);border-radius:var(--radius);background:transparent;color:var(--text-dim);font:inherit;font-size:10.5px;font-weight:650;padding:5px 6px;cursor:pointer;text-align:center}
.railsave:hover{color:var(--accent);border-color:var(--accent)}
.railsave.on{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 55%,var(--border));background:color-mix(in srgb,var(--accent) 8%,transparent)}
.cnamein{flex:1;min-width:0;font:inherit;font-size:12px;font-weight:600;color:var(--text);background:var(--surface-1);border:1px solid var(--accent);border-radius:4px;padding:1px 5px;outline:none}
.cnav{display:flex;align-items:center;gap:6px;width:100%;border:0;background:transparent;color:var(--text-dim);font:inherit;font-size:11px;font-weight:600;padding:5px 6px;border-radius:var(--radius);cursor:pointer;text-align:left}
.cnav:hover{background:var(--surface-3);color:var(--text)}
.cnav.on{background:var(--nav-active);color:var(--accent)}
.cnav .cdot2,.cnav .gl{width:8px;flex-shrink:0}
.cnav .gl{text-align:center;font-size:11px}
.cnav .cn{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cnav .cc{margin-left:auto;color:var(--text-faint);font-weight:500;font-variant-numeric:tabular-nums;font-size:10px}
.cnav.on .cc{color:var(--accent)}
/* group chevron (blank when a group has no subgroups, so names stay aligned); subgroups indent under it */
.cnav .chv{width:9px;flex-shrink:0;margin-right:-2px;text-align:center;font-size:9px;color:var(--text-faint)}
.cnav .chv:hover{color:var(--text)}
.cnav.child{padding-left:25px;font-weight:500}
.cnav.child.own .cn{font-style:italic} /* "General" = the group's own category, without its subgroups */
/* the composition's header: count + ＋ New block (into the rail's selection) */
/* pinned while the list scrolls (ui-design.md: action rows don't scroll away) */
.comphd{position:sticky;top:-8px;z-index:2;display:flex;align-items:center;justify-content:space-between;gap:8px;flex-shrink:0;
  margin:-8px -8px 0;padding:8px 8px 5px;background:var(--surface-1)}
.comphd .zlbl{padding:0 2px;height:auto}
.newblk{display:inline-flex;align-items:center;gap:6px;border:1px dashed var(--border-strong);background:transparent;color:var(--text-dim);
  border-radius:6px;font:inherit;font-size:11px;font-weight:650;padding:3px 9px;cursor:pointer;white-space:nowrap}
.newblk:hover{color:var(--accent);border-color:var(--accent);background:color-mix(in srgb,var(--accent) 6%,transparent)}
.newblk i{width:8px;height:8px;border-radius:50%;display:inline-block}
/* a row's colour dot is the group picker's handle */
.cdot2.pick{width:11px;height:11px;border:0;padding:0;cursor:pointer;box-shadow:0 0 0 2px transparent;transition:box-shadow .12s}
.cdot2.pick:hover,.cdot2.pick.act{box-shadow:0 0 0 2px var(--surface-2),0 0 0 4px var(--accent)}
/* the group picker (teleported) */
.cpk-back{position:fixed;inset:0;z-index:2600}
.cpk{position:fixed;z-index:2601;width:236px;max-height:360px;overflow-y:auto;background:var(--surface-1);border:1px solid var(--border-strong);
  border-radius:var(--radius-lg);box-shadow:0 14px 36px rgba(0,0,0,.45);padding:5px;display:flex;flex-direction:column}
.cpk-hd{font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint);padding:4px 8px 5px}
.cpk-o{display:flex;align-items:center;gap:8px;border:0;background:transparent;color:var(--text-dim);font:inherit;font-size:12px;
  text-align:left;padding:5px 8px;border-radius:5px;cursor:pointer}
.cpk-o i{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.cpk-o:hover,.cpk-o.on{background:var(--surface-3);color:var(--text)}
.cpk-o.grp{font-weight:650;color:var(--text)}
.cpk-o.child{padding-left:26px}
.cpk-o.virt{cursor:default;font-size:10.5px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;color:var(--text-faint);padding-top:7px}
.cpk-o.virt:hover{background:transparent}
.cpk-o .ck{margin-left:auto;color:var(--accent);font-weight:800}
.cpk-sep{height:1px;background:var(--border);margin:4px 2px;flex-shrink:0}
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
