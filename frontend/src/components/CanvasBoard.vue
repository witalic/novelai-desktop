<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { NodeResizer } from '@vue-flow/node-resizer'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/node-resizer/dist/style.css'
import { getAppSettings, getVaultConfig, saveDownloads, saveWork } from '../api'
import { useToast } from '../composables/useToast'
import { canvasToWork, workToCanvas } from '../vault/serialize'
import { newId } from '../vault/ids'
import { onBeforeQuit } from '../electron'
import type { GenResult, LibraryBlock, PanelParams, SnapshotData, WorkDoc } from '../types'

const toast = useToast()
const props = defineProps<{
  drafts: GenResult[]; busy: boolean; error: string; preview: string; params: PanelParams
  openWork: WorkDoc | null; insertBlocks?: { blocks: LibraryBlock[]; nonce: number } | null
}>()
const emit = defineEmits<{
  generate: [{ positive: string; negative: string; snapshot: SnapshotData }]
  take: []
  navigate: [string]
}>()

const {
  nodes, addNodes, removeNodes, findNode, onNodeDragStop, getIntersectingNodes, viewport, screenToFlowCoordinate,
  setNodes, setViewport, onNodeContextMenu, onSelectionContextMenu, onPaneContextMenu,
} = useVueFlow()

// The "station" is one coupled node: [ Output | Positive / Negative lanes ] + header + meta.
// Internal areas are ratio-driven (data.outputRatio, data.posRatio) so they scale on resize + splitters.
const STATION = 'station'
const HEADER = 44
const META = 66
// The three anchor zones. Content inside them is saved; anything loose on the canvas is a draft.
const LIBRARY = 'library'
const GALLERY = 'gallery'
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
// Loose blocks/images not inside any anchor zone are drafts — they won't be saved.
const orphanCount = computed(() => nodes.value.filter((n) => (n.type === 'block' || n.type === 'image') && !ANCHORS.has(n.parentNode ?? '')).length)
const childCount = (id: string) => nodes.value.filter((n) => n.parentNode === id).length

let blockSeq = 0
const flowRef = ref<HTMLElement | null>(null)
const topSelected = ref(false)

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
  const draft = props.drafts[0]
  if (!draft) return
  const pos = toFlow(e.clientX, e.clientY)
  const ar = (draft.params.width || 832) / (draft.params.height || 1216)
  const w = ar >= 1 ? 180 : Math.round(180 * ar)
  const h = ar >= 1 ? Math.round(180 / ar) : 180
  const id = draft.id // reuse the draft's id/file so a restored draft keeps its image when kept
  // The generation recipe (prompt + params) lives in the snapshot; the node keeps only display + meta.
  // `ar` is the true source aspect ratio — resizing derives sizes from it so rounding never accumulates.
  const data = { url: draft.url, file: draft.file || '', snapshot: draft.snapshot, ar, created_at: new Date().toISOString() }
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

// ---- image scaling to reference multiples + grid arrange ----
const SCALES = [0.5, 1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 9, 10]
const BASE_LONG = 180 // the long side at scale ×1
function snapScale(v: number) {
  return SCALES.reduce((best, s) => (Math.abs(s - v) < Math.abs(best - v) ? s : best), SCALES[0])
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function scaleOf(n: any) {
  const d = dims(n)
  return snapScale(Math.max(d.w, d.h) / BASE_LONG)
}
function imgScale(id: string) {
  const n = findNode(id)
  return n ? `×${scaleOf(n)}` : ''
}
// The <img> is laid out at a chosen long side (→ decoded at that size) then transform-scaled down to the
// node box, so the decode never collapses to a tiny display size (which smears when scaled back up). The
// long side = node size × device-pixel-ratio × a supersample headroom, floored so it can't go tiny and
// capped at the source resolution.
const DEC_FLOOR = 480 // never fetch below this long side (crisp when scaled small)
const DEC_CEIL = 1920 // up to the source's long side — big scales get full detail (backend never upscales)
// Supersample headroom: fetching more pixels than the screen shows and letting the browser downscale
// antialiases → sharper edges. It TAPERS with the image count so a large work (100-200) can't blow
// Chromium's decode-memory budget (which would make it downsample cached bitmaps → pixelation), while a
// normal work keeps maximum sharpness. Bucketed so it only steps at coarse thresholds — adding/removing a
// single image never re-fetches every derivative. Viewport culling handles the zoomed-in case; this bounds
// the zoomed-out "all images visible at once" case.
const imageCount = computed(() => nodes.value.reduce((c, n) => c + (n.type === 'image' ? 1 : 0), 0))
const headroom = computed(() => {
  const n = imageCount.value
  return n <= 30 ? 3.5 : n <= 60 ? 2.6 : n <= 120 ? 1.9 : 1.4
})
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function decodeDims(id: string, data: any) {
  const n = findNode(id)
  const box = n ? sizeOf(n) : { w: 180, h: 320 }
  const ar = data.ar || box.w / box.h || 832 / 1216
  const dpr = Math.min(Math.max(window.devicePixelRatio || 1, 1), 3) // physical pixels per CSS px
  const nl = Math.min(DEC_CEIL, Math.max(DEC_FLOOR, Math.ceil(Math.max(box.w, box.h) * headroom.value * dpr)))
  const nw = ar >= 1 ? nl : Math.round(nl * ar)
  const nh = ar >= 1 ? Math.round(nl / ar) : nl
  return { box, nw, nh }
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function fullStyle(id: string, data: any) {
  const { box, nw, nh } = decodeDims(id, data)
  return { width: `${nw}px`, height: `${nh}px`, transform: `scale(${box.w / nw})`, transformOrigin: 'top left' }
}
// Vault-stored images are fetched right-sized (`?w=`) so the browser never decodes the full-resolution
// source only to shrink it — a server-sent thumbnail decodes correctly on any display/DPR and can't be
// downsampled under decode-memory pressure. Freshly generated images are data: URIs and stay as-is.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function imgSrc(id: string, data: any) {
  if (!data.url || data.url.startsWith('data:')) return data.url
  return `${data.url}?w=${decodeDims(id, data).nw}`
}
// Flash-free resolution swap: when a node's target ?w= changes we preload the new derivative off-screen
// and only swap the visible <img> once it has decoded — the current image stays put meanwhile, so a
// resolution change never blanks the card. `shownSrc` is seeded when a node first appears (seedSrc) so
// there is always an "old" src to hold during the swap.
const shownSrc = ref<Record<string, string>>({})
// Record the current target as shown, immediately (no preload) — for a node that has no visible image yet.
function seedSrc(id: string) {
  const n = findNode(id)
  if (n && n.type === 'image' && !shownSrc.value[id]) shownSrc.value[id] = imgSrc(id, n.data)
}
// Swap to the node's current target, preloading first so the card never blanks. Called imperatively from
// the events that actually change the target (scale, headroom bucket) — NOT from a deep node watcher,
// which would re-run per drag frame (O(images) each) and dominate drag cost at large works.
function swapSrc(id: string) {
  const n = findNode(id)
  if (!n || n.type !== 'image') return
  const want = imgSrc(id, n.data)
  if (!want || shownSrc.value[id] === want) return
  if (!shownSrc.value[id] || want.startsWith('data:')) { shownSrc.value[id] = want; return } // no old to hold
  const pre = new Image()
  pre.onload = () => { const cur = findNode(id); if (cur && imgSrc(id, cur.data) === want) shownSrc.value[id] = want }
  pre.onerror = () => { /* keep the current (older) src on a failed derivative rather than blanking the card */ }
  pre.src = want
}
// When the supersample bucket steps (work grew/shrank past a threshold), re-target every image.
watch(headroom, () => { for (const n of nodes.value) if (n.type === 'image') swapSrc(n.id) })
// Set an image to a reference scale (long side = BASE_LONG × scale), keeping its aspect ratio.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function applyScale(node: any, scale: number) {
  const live = findNode(node.id)
  if (!live) return
  // Aspect ratio comes from a stable source (stored `ar`), never re-derived from the last rounded size —
  // otherwise integer rounding accumulates each resize and object-fit:cover crops the image progressively.
  // Restored works predating `ar` cache it once from their still-unscaled dimensions.
  if (live.data.ar == null) { const d0 = dims(live); live.data = { ...live.data, ar: d0.w / d0.h } }
  const ar = live.data.ar
  const long = BASE_LONG * scale
  const w = Math.round(ar >= 1 ? long : long * ar)
  const h = Math.round(ar >= 1 ? long / ar : long)
  live.style = { width: `${w}px`, height: `${h}px` }
  swapSrc(live.id) // scale changed the target ?w= → preload + swap without blanking
}
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
const PICK_SCALES = SCALES.filter((s) => s <= 5)
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
  await flush(true)
  workId.value = newId('work')
  title.value = ''
  setNodes(zoneNodes())
  shownSrc.value = {} // no images in a fresh work — drop the previous work's entries
  setViewport({ x: 40, y: 40, zoom: 0.7 })
  lastSig = changeKey()
  dirty = false
  saveState.value = 'idle'
  savedAt.value = ''
}

onMounted(async () => {
  try { vaultReady.value = !!(await getVaultConfig()).active } catch { /* backend not ready */ }
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
      // Not in the composition — drop into the Library quick-access zone, or detach to draft.
      const lib = getIntersectingNodes(node).find((n) => n.id === LIBRARY)
      if (lib) {
        const lp = lib.computedPosition
        if (live.parentNode !== LIBRARY) live.position = { x: node.computedPosition.x - lp.x, y: node.computedPosition.y - lp.y }
        live.parentNode = LIBRARY
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

// A block "used" from the Library tab lands in the canvas Library zone, keeping its vault link
// (block_id/version/tags) so generated snapshots and inherited image tags stay traceable.
function insertLibraryBlock(b: LibraryBlock) {
  blockSeq += 1
  const stacked = childCount(LIBRARY)
  addNodes([{
    id: `lib-${b.id}-${blockSeq}`, type: 'block', parentNode: LIBRARY, zIndex: 2,
    position: { x: 16, y: 52 + stacked * 46 }, style: { width: '176px' },
    data: {
      category: b.category, name: b.name, text: b.text, polarity: b.polarity, expanded: false,
      block_id: b.id, version: b.version ?? 1, tags: b.tags ?? [],
    },
  }])
}
watch(() => props.insertBlocks?.nonce, () => { props.insertBlocks?.blocks.forEach(insertLibraryBlock) })
function doGenerate() {
  const components = compBlocks.value.slice().sort((a, b) => a.position.x - b.position.x).map((b) => ({
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

// ---- vault: title + save (dirty-flagged; flushed on leave / close / timer), manual save, load ----
// Not a per-keystroke debounce: edits just mark the work dirty, and it's persisted at meaningful
// moments (leaving Generate, app close, a periodic timer) — heavy works never save every few seconds.
type SaveState = 'idle' | 'dirty' | 'saving' | 'saved'
const title = ref('')
const vaultReady = ref(false)
const workId = ref(newId('work'))
const saveState = ref<SaveState>('idle')
const savedAt = ref('')

let dirty = false
let saving = false
let pendingResave = false
let lastSig = ''

// A work is worth persisting once it has a title, a kept gallery image, OR a non-empty prompt block in the
// station/library — otherwise an assembled-but-ungenerated composition would be discarded silently on leave.
function isMeaningful() {
  return title.value.trim().length > 0 || nodes.value.some((n) =>
    (n.type === 'image' && n.parentNode === GALLERY)
    || (n.type === 'block' && (n.parentNode === STATION || n.parentNode === LIBRARY)
        && String(n.data?.text || '').trim().length > 0))
}

// Cheap change signature (excludes image bytes) so an unchanged work is never re-written.
function changeKey(): string {
  const sig = nodes.value.map((n) => ({
    i: n.id, p: n.parentNode ?? null,
    x: Math.round(n.position?.x ?? 0), y: Math.round(n.position?.y ?? 0),
    s: n.style, d: n.type === 'image' ? (n.data?.url ? 1 : 0) : n.data,
  }))
  return JSON.stringify({ t: title.value, params: props.params, nodes: sig, stack: props.drafts.map((d) => d.id) })
}

function markDirty() {
  if (!isMeaningful()) return
  dirty = true
  if (saveState.value !== 'saving') saveState.value = 'dirty'
}

// Persist if there is something new. Coalesces concurrent triggers (one save at a time).
async function flush(force = false) {
  if (!vaultReady.value || !isMeaningful()) return
  if (saving) { pendingResave = true; return }
  if (!force && !dirty) return
  const key = changeKey()
  if (key === lastSig) { dirty = false; if (saveState.value === 'dirty') saveState.value = 'saved'; return }
  saving = true
  saveState.value = 'saving'
  try {
    await saveWork(canvasToWork(nodes.value, viewport.value, props.params, { id: workId.value, title: title.value }, props.drafts) as WorkDoc)
    lastSig = key
    dirty = false
    saveState.value = 'saved'
    savedAt.value = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch (e) {
    dirty = true
    saveState.value = 'dirty'
    toast.push(e instanceof Error ? e.message : 'Save failed', 'err')
  } finally {
    saving = false
    if (pendingResave) { pendingResave = false; flush() }
  }
}

// Manual save (button / ⌘S). With no vault, route the user to Settings.
function manualSave() {
  if (!vaultReady.value) { emit('navigate', 'settings'); return }
  flush(true)
}
function onKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); manualSave(); return }
  if (e.key === 'Delete' || e.key === 'Backspace') {
    const t = e.target as HTMLElement | null
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return // typing, not deleting
    const ids = nodes.value.filter((n) => n.selected && REMOVABLE.has(n.type)).map((n) => n.id)
    if (ids.length) { e.preventDefault(); removeNodes(ids) }
  }
}

// Browser fallback for window close (Electron uses onBeforeQuit); best-effort, size-limited.
function onBeforeUnload() {
  if (!vaultReady.value || !dirty || saving || !isMeaningful() || changeKey() === lastSig) return
  const doc = canvasToWork(nodes.value, viewport.value, props.params, { id: workId.value, title: title.value }, props.drafts)
  try { navigator.sendBeacon('/api/vault/works', new Blob([JSON.stringify(doc)], { type: 'application/json' })) } catch { /* best-effort */ }
}

// Periodic auto-save; interval comes from Settings and is re-read when returning to Generate.
let autosaveTimer: ReturnType<typeof setInterval> | null = null
async function refreshInterval() {
  let ms = 300_000
  try { ms = Math.max(30, (await getAppSettings()).autosave_interval_s) * 1000 } catch { /* keep default */ }
  if (autosaveTimer) clearInterval(autosaveTimer)
  autosaveTimer = setInterval(() => { if (dirty) flush() }, ms)
}

watch([nodes, () => props.params, () => props.drafts, title], markDirty, { deep: true })

let disposeBeforeQuit: (() => void) | null = null
onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('beforeunload', onBeforeUnload)
  disposeBeforeQuit = onBeforeQuit(() => flush()) // Electron: main waits for this before quitting
  refreshInterval()
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('beforeunload', onBeforeUnload)
  disposeBeforeQuit?.() // unregister so the IPC listener never stacks across remounts
  if (autosaveTimer) clearInterval(autosaveTimer)
})
onActivated(refreshInterval)                 // returning to Generate → pick up a changed interval
onDeactivated(() => { if (dirty) flush() })  // leaving Generate → flush now

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function loadDoc(doc: any) {
  const { nodes: ns, viewport: vp } = workToCanvas(doc)
  setNodes(ns)
  shownSrc.value = {} // drop the previous work's entries, then seed this work's images
  for (const n of ns) if (n.type === 'image') seedSrc(n.id)
  if (vp) setViewport(vp)
  workId.value = doc.id
  title.value = doc.title || ''
  lastSig = changeKey() // the loaded state is the baseline — not dirty
  dirty = false
  saveState.value = 'saved'
  savedAt.value = ''
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
  }
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

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
      <div v-if="orphanCount" class="orphan-banner">⚠ {{ orphanCount }} item{{ orphanCount > 1 ? 's' : '' }} outside the zones — won't be saved</div>
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
              <button class="gzgen nodrag" :disabled="busy || !composed.positive" @pointerdown.stop @click.stop="doGenerate">Generate</button>
            </div>
            <div class="stbody">
              <div class="stoutput" :style="{ flexGrow: data.outputRatio ?? 0.3 }">
                <div class="colhd">Output</div>
                <div class="outbody">
                  <img v-if="busy && preview" class="liveprev" :src="preview" alt="generating preview" />
                  <div v-else-if="drafts.length" class="topwrap nodrag" :class="{ selected: topSelected }" draggable="true"
                    @dragstart="onDraftDragStart" @pointerdown.stop @click.stop="topSelected = !topSelected" title="Drag onto the canvas to keep">
                    <img class="topimg" :src="drafts[0].url" alt="latest generation" draggable="false" />
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
          <NodeResizer :min-width="200" :min-height="180" :is-visible="selected" color="var(--accent)" />
          <div class="zonenode" :class="[data.role, { selected }]">
            <div class="zonehd">
              <span class="zicon">{{ data.role === 'library' ? '✦' : '▤' }}</span>
              <span class="ztitle">{{ data.role === 'library' ? 'Library' : 'Gallery' }}</span>
              <span class="anchor-tag">anchor</span>
            </div>
            <div v-if="!childCount(id)" class="zhint">
              {{ data.role === 'library' ? 'Drag prompt blocks here for quick access.' : 'Drag kept images here to save them.' }}
            </div>
          </div>
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
.orphan-banner{position:absolute;top:12px;left:50%;transform:translateX(-50%);z-index:5;pointer-events:none;
  display:flex;align-items:center;font-size:12px;font-weight:600;color:#e8913a;padding:6px 14px;border-radius:20px;
  background:color-mix(in srgb,#b65c02 16%,var(--surface-1));border:1px solid color-mix(in srgb,#b65c02 45%,var(--border));
  box-shadow:0 4px 16px rgba(0,0,0,.35)}
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

.imgnode{position:relative;width:100%;height:100%;min-width:72px;min-height:104px;border-radius:8px;overflow:hidden;border:1px solid var(--border);background:var(--surface-2);box-shadow:0 2px 6px rgba(0,0,0,.25)}
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
