<script setup lang="ts">
// Structured gallery block-stack — a REUSABLE renderer of a work's gallery-role images as a composable
// stack of typed blocks (Section/Heading/Text/Image-grid/Metadata/Divider) plus Outline + Quick access.
// Hosted by the canvas node (GalleryWidget usage in CanvasBoard) and, from Phase 2, the full-page Works
// view/edit. The host owns the data (`data.blocks` + `images`) and handles the emitted ops; `readonly`
// (View mode) suppresses the editing affordances. Design: design/gallery-widget-mockup.html.
import { computed, nextTick, ref } from 'vue'
import { hiddenBlockIds } from './galleryBlocks'
import { newId } from '../vault/ids'
import type { GalleryBlock, GalleryMetaField, ImageNodeData, ZoneNode } from '../types'

// Minimal shape the stack reads off a gallery image node (avoids coupling to Vue Flow's node type).
interface GalleryImage { id: string; data: Partial<ImageNodeData> }

const props = defineProps<{
  data: ZoneNode['data'] // holds `blocks` — mutated in place (host tracks + persists)
  images: GalleryImage[] // the work's gallery-role images
  title?: string
  selected?: boolean // host selection → accent border (matches the other canvas widgets)
  readonly?: boolean // View mode: hide add/edit/delete/drag affordances (Phase 2 Works view)
  embedded?: boolean // full-page host (Works): drop the node chrome (border/radius) + the duplicated title
}>()
const emit = defineEmits<{
  favorite: [string]; preview: [string]
  toQuick: [string] // move an image out of its grid into Quick access (unassign)
  deleteImg: [string] // delete an image from the work entirely (guarded by a confirm in the parent)
  clearQuick: [string[]] // delete every unsorted image except favourites (guarded by a confirm in the parent)
  download: [string[]] // save these image URLs to the Downloads folder (parent owns the download pipeline)
  dropOnGrid: [{ gridId: string; imageId?: string; payload?: string }] // a thumb or kept-draft dropped onto a specific grid
  dropOnQuick: [{ imageId?: string; payload?: string }] // dropped onto Quick access → unassigned gallery image
}>()

// Quick access = the default unordered bin: gallery images not assigned to any grid. New generations and
// anything dropped outside a specific grid land here; the user drags them into grids to curate. Derived.
// Panel open-state persists with the work (layout on the zone data — mutated in place, tracked, autosaved).
const quickOpen = computed({ get: () => !!props.data.quickOpen, set: (v) => { props.data.quickOpen = v } })
const assignedIds = computed(() => new Set(blocks.value.flatMap((b) => (b.type === 'grid' ? b.imageIds : []))))
const unassigned = computed(() => props.images.filter((im) => !assignedIds.value.has(im.id)))
const clearableQuick = computed(() => unassigned.value.filter((im) => !im.data.favorite).map((im) => im.id)) // deletable = unsorted & not favourited
function urlsOf(imgs: GalleryImage[]): string[] { return imgs.map((im) => im.data.url).filter((u): u is string => !!u) }
// Imprecise drops inside the widget (on a section header, heading/text/meta block, the outline, or a
// gap) would otherwise bubble to the canvas and yank the thumbnail into a hidden scratch node (H4).
// Swallow stray `nai-galimg` moves as a no-op; let draft payloads fall through (dropping a kept draft
// onto the gallery still adds it via the canvas handler).
function onRootDrop(e: DragEvent) {
  const payload = e.dataTransfer?.getData('text/plain') || ''
  if (payload.startsWith('nai-galimg:')) { e.preventDefault(); e.stopPropagation() }
}
const quickDropOver = ref(false)
function onQuickDrop(e: DragEvent) {
  quickDropOver.value = false
  const payload = e.dataTransfer?.getData('text/plain') || ''
  if (payload.startsWith('nai-galimg:')) emit('dropOnQuick', { imageId: payload.slice('nai-galimg:'.length) })
  else if (payload === 'nai-draft' || payload.startsWith('nai-draft:') || payload.startsWith('nai-drafts:')) emit('dropOnQuick', { payload })
}

// A grid is a drop target: a thumbnail dragged from another grid MOVES here; a kept draft dropped
// here joins this album (CanvasBoard resolves the payload — it owns the drafts + the image nodes).
const gridDropTarget = ref<string | null>(null)
function onGridDrop(b: GalleryBlock, e: DragEvent) {
  gridDropTarget.value = null
  if (b.type !== 'grid') return
  const payload = e.dataTransfer?.getData('text/plain') || ''
  if (payload.startsWith('nai-galimg:')) emit('dropOnGrid', { gridId: b.id, imageId: payload.slice('nai-galimg:'.length) })
  else if (payload === 'nai-draft' || payload.startsWith('nai-draft:') || payload.startsWith('nai-drafts:')) emit('dropOnGrid', { gridId: b.id, payload })
}

const blocks = computed<GalleryBlock[]>(() => props.data.blocks ?? [])
const total = computed(() => props.images.length)

// Each grid is an album owning an ordered `imageIds` list; resolve them to image nodes for rendering.
const imageById = computed(() => new Map(props.images.map((im) => [im.id, im])))
function gridImages(b: GalleryBlock): GalleryImage[] {
  if (b.type !== 'grid' || !Array.isArray(b.imageIds)) return []
  return b.imageIds.map((id) => imageById.value.get(id)).filter((im): im is GalleryImage => !!im)
}
// Collapsed grid shows a single row (its column count); the rest fold away behind a "+N" tile.
function gridVisible(b: GalleryBlock): GalleryImage[] {
  const all = gridImages(b)
  return b.type === 'grid' && b.collapsed ? all.slice(0, b.cols) : all
}
function gridHidden(b: GalleryBlock): number {
  return b.type === 'grid' && b.collapsed ? Math.max(0, gridImages(b).length - b.cols) : 0
}

const dpr = Math.min(typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1, 2)
// Server-sized thumbnail for a cell; a fresh `data:` URL can't be resized so it's used as-is.
// View mode serves the full-resolution original — the `?w=` downscaling is a canvas-perf trade-off,
// not something to inflict on a full-page reading view (the earlier softness was a bug there).
function thumbSrc(url: string | undefined, cols: number): string {
  if (!url || url.startsWith('data:')) return url || ''
  if (props.embedded) return url // full-page Works host (view or edit): full-resolution, no downscale
  const cellPx = Math.round(700 / cols) // node ≈ 700px wide inside padding
  return `${url}?w=${Math.round(cellPx * dpr * 1.4)}`
}

function setCols(b: GalleryBlock, cols: 2 | 3 | 4 | 5 | 6 | 7 | 8) { if (b.type === 'grid') b.cols = cols } // in-place → tracked + autosaved

// ---- compose the block stack (add / delete; drag-reorder lands in a later increment) ----
type BlockType = GalleryBlock['type']
const ADD_TYPES: { type: BlockType; label: string; glyph: string; hint: string }[] = [
  { type: 'section', label: 'Section', glyph: '▤', hint: 'a collapsible group' },
  { type: 'heading', label: 'Heading', glyph: 'H', hint: 'a title' },
  { type: 'text', label: 'Text', glyph: '¶', hint: 'a description / note' },
  { type: 'grid', label: 'Image grid', glyph: '▦', hint: 'images in fixed cells' },
  { type: 'meta', label: 'Metadata', glyph: '≣', hint: 'tags · dates · model · seed' },
  { type: 'divider', label: 'Divider', glyph: '—', hint: 'a thin rule' },
]

// Metadata auto-fields, summarised across a set of gallery images/snapshots (never persisted).
type MetaSummary = { tags: string[]; date: string; model: string; seed: string; dimensions: string }
// An image's tags = its manual tags + the tags of the positive prompt blocks it was generated from
// (frozen in the snapshot's components at generation — the block tags carried onto the image).
function imageTags(im: GalleryImage): string[] {
  const fromBlocks = (im.data.snapshot?.components || [])
    .filter((c) => c.polarity === 'positive')
    .flatMap((c) => c.tags || [])
  return [...(im.data.tags || []), ...fromBlocks]
}
function autoMetaOf(imgs: GalleryImage[]): MetaSummary {
  const tagCount = new Map<string, number>()
  const models = new Set<string>(), seeds = new Set<string>(), dims = new Set<string>()
  let minD = '', maxD = ''
  for (const im of imgs) {
    for (const t of new Set(imageTags(im))) tagCount.set(t, (tagCount.get(t) ?? 0) + 1) // count each tag once per image
    const p = (im.data.snapshot?.params || {}) as Record<string, unknown>
    if (p.model) models.add(String(p.model))
    if (p.seed != null) seeds.add(String(p.seed))
    if (p.width && p.height) dims.add(`${p.width}×${p.height}`)
    const d = im.data.created_at || ''
    if (d) { if (!minD || d < minD) minD = d; if (!maxD || d > maxD) maxD = d }
  }
  const day = (s: string) => s.slice(0, 10)
  const one = (set: Set<string>, plural: string) => set.size === 1 ? [...set][0] : set.size ? `${set.size} ${plural}` : '—'
  return {
    tags: [...tagCount.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([t]) => t), // most-frequent first
    date: minD ? (day(minD) === day(maxD) ? day(minD) : `${day(minD)} – ${day(maxD)}`) : '—',
    model: one(models, 'models'), seed: seeds.size <= 1 ? ([...seeds][0] ?? '—') : 'mixed', dimensions: one(dims, 'sizes'),
  }
}
// A meta block summarises either one bound grid (gridId) or, by default, the whole gallery (item 3).
function metaImages(b: GalleryBlock): GalleryImage[] {
  if (b.type === 'meta' && b.gridId) {
    const g = blocks.value.find((x) => x.id === b.gridId && x.type === 'grid')
    if (g) return gridImages(g)
  }
  return props.images
}
const metaSummaries = computed(() => {
  const m = new Map<string, MetaSummary>()
  for (const b of blocks.value) if (b.type === 'meta') m.set(b.id, autoMetaOf(metaImages(b)))
  return m
})
function summaryFor(b: GalleryBlock): MetaSummary { return metaSummaries.value.get(b.id) ?? autoMetaOf([]) }
const TAG_CAP = 24 // cap the tag chips shown in a meta block; the rest fold into a "+N more"
function metaChips(b: GalleryBlock, f: GalleryMetaField): string[] {
  if (f.auto === 'tags') return summaryFor(b).tags
  return Array.isArray(f.value) ? f.value : []
}
function metaText(b: GalleryBlock, f: GalleryMetaField): string {
  if (f.auto) return String((summaryFor(b) as unknown as Record<string, string | string[]>)[f.auto] ?? '—')
  return typeof f.value === 'string' ? f.value : ''
}
function addMetaField(b: GalleryBlock) { if (b.type === 'meta') b.fields.push({ key: 'Field', kind: 'text', value: '' }) }
function removeMetaField(b: GalleryBlock, i: number) { if (b.type === 'meta') b.fields.splice(i, 1) }

// ---- meta block scope: bind its summary to a specific grid (or the whole gallery) ----
const metaScopeOpen = ref<string | null>(null) // which meta block's scope dropdown is open — transient
const gridOptions = computed(() =>
  blocks.value.filter((b) => b.type === 'grid').map((b, i) => ({ id: b.id, label: `Grid ${i + 1}`, count: (b.type === 'grid' && Array.isArray(b.imageIds)) ? b.imageIds.length : 0 })))
function scopeLabel(b: GalleryBlock): string {
  if (b.type !== 'meta' || !b.gridId) return 'Whole gallery'
  return gridOptions.value.find((g) => g.id === b.gridId)?.label ?? 'Whole gallery'
}
function setMetaScope(b: GalleryBlock, gridId?: string) {
  if (b.type === 'meta') { if (gridId) b.gridId = gridId; else delete b.gridId } // in-place → tracked + autosaved
  metaScopeOpen.value = null
}

// Drag a thumbnail: reorder within its grid (drop on a sibling), move to another grid/Quick, or out
// onto the canvas (CanvasBoard reads the `nai-galimg:` payload). `imgDragId` tracks the in-widget drag.
const imgDragId = ref<string | null>(null)
const imgDropId = ref<string | null>(null)
const imgDropAfter = ref(false) // caret side: insert after the hovered thumb (else before)
function onThumbDrag(id: string, e: DragEvent) {
  imgDragId.value = id
  if (e.dataTransfer) { e.dataTransfer.setData('text/plain', `nai-galimg:${id}`); e.dataTransfer.effectAllowed = 'copyMove' }
}
function onThumbDragEnd() { imgDragId.value = null; imgDropId.value = null }
// Show an insertion caret before/after the hovered thumb (by which half the pointer is over) — NO array
// mutation while dragging, so it stays smooth and the landing spot is explicit. The move commits on drop.
function onThumbOver(targetId: string, e: DragEvent) {
  if (!imgDragId.value || targetId === imgDragId.value) { imgDropId.value = null; return }
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
  imgDropId.value = targetId
  imgDropAfter.value = e.clientX > r.left + r.width / 2
}
// Commit the move at the caret: reorder within this grid, or pull in from another grid / Quick, then place.
function onThumbDrop(b: GalleryBlock, targetId: string, e: DragEvent) {
  const from = imgDragId.value, after = imgDropAfter.value
  imgDragId.value = null; imgDropId.value = null
  if (b.type !== 'grid' || !Array.isArray(b.imageIds)) return
  if (!from) { // not an internal thumb drag → a kept draft dropped exactly on a tile joins this album (M6)
    const payload = e.dataTransfer?.getData('text/plain') || ''
    if (payload === 'nai-draft' || payload.startsWith('nai-draft:') || payload.startsWith('nai-drafts:')) emit('dropOnGrid', { gridId: b.id, payload })
    return
  }
  if (targetId === from) return
  const place = () => {
    const fi = b.imageIds.indexOf(from)
    if (fi >= 0) b.imageIds.splice(fi, 1) // lift out of its current slot (same grid)
    const at = b.imageIds.indexOf(targetId)
    if (at < 0) return
    b.imageIds.splice(after ? at + 1 : at, 0, from)
  }
  if (b.imageIds.includes(from)) place() // reorder within this grid
  else { emit('dropOnGrid', { gridId: b.id, imageId: from }); nextTick(place) } // pull into this album, then position at the caret
}
// Blocks after a collapsed section are hidden until the next section (v-show, not v-if, so their DOM
// scroll/focus survives the collapse — UI-design ledger). Range logic is unit-tested in gallerySource.
const hiddenIds = computed(() => hiddenBlockIds(blocks.value))
// Section grouping (feedback item 1): each section + the blocks it owns render as one tinted, bracketed
// band, and consecutive sections get distinct colours so it's obvious at a glance what belongs together.
const SECTION_HUES = [210, 150, 275, 32, 338, 190, 95]
interface SecInfo { secIdx: number; head: boolean; member: boolean; last: boolean }
const sectionInfo = computed(() => {
  const map = new Map<string, SecInfo>()
  const arr = blocks.value
  let secIdx = -1
  for (let i = 0; i < arr.length; i++) {
    const b = arr[i]
    if (b.type === 'section') { secIdx++; map.set(b.id, { secIdx, head: true, member: false, last: false }) }
    else if (secIdx >= 0) {
      const next = arr[i + 1]
      map.set(b.id, { secIdx, head: false, member: true, last: !next || next.type === 'section' })
    }
  }
  return map
})
function secColor(id: string): string | undefined {
  const info = sectionInfo.value.get(id)
  return info ? `hsl(${SECTION_HUES[info.secIdx % SECTION_HUES.length]} 62% 56%)` : undefined
}
function sectionMemberCount(id: string): number {
  const info = sectionInfo.value.get(id)
  if (!info) return 0
  let c = 0
  for (const v of sectionInfo.value.values()) if (v.member && v.secIdx === info.secIdx) c++
  return c
}
const addOpen = ref(false) // Add-block menu open — transient
function toggleAdd() { addOpen.value = !addOpen.value }
function newBlock(type: BlockType): GalleryBlock {
  const id = newId('gb')
  if (type === 'section') return { id, type, title: 'Section', collapsed: false }
  if (type === 'heading') return { id, type, text: 'Heading', level: 2 }
  if (type === 'text') return { id, type, text: '' }
  if (type === 'grid') return { id, type, imageIds: [], cols: 3 }
  if (type === 'meta') return { id, type, fields: [
    { key: 'Tags', kind: 'chips', auto: 'tags' },
    { key: 'Date', kind: 'text', auto: 'date' },
    { key: 'Model', kind: 'mono', auto: 'model' },
    { key: 'Seed', kind: 'mono', auto: 'seed' },
  ] }
  return { id, type: 'divider' }
}
// A block is "selected" by clicking its body (not an interactive control) — a new block lands right
// after it (i.e. into the same section), so creation is predictable (feedback item 7).
const selectedId = ref<string | null>(null)
function selectBlock(id: string) { selectedId.value = id }
function addBlock(type: BlockType) {
  const arr = (props.data.blocks ??= [])
  const at = selectedId.value ? arr.findIndex((b) => b.id === selectedId.value) : -1
  const block = newBlock(type)
  if (at >= 0) arr.splice(at + 1, 0, block); else arr.push(block) // after the selected block, else append
  selectedId.value = block.id // keep the chain going — the next Add lands after this one
  addOpen.value = false
}
function deleteBlock(id: string) {
  const arr = props.data.blocks
  const i = arr ? arr.findIndex((b) => b.id === id) : -1
  if (arr && i >= 0) arr.splice(i, 1)
  if (selectedId.value === id) selectedId.value = null
}
// Inline edit for heading/text — v-model mutates the block in place; no contenteditable cursor issues.
function autogrow(e: Event) {
  const el = e.target as HTMLTextAreaElement
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

// ---- drag-to-reorder the block stack (HTML5 drag off a grip; the node itself never moves) ----
const dragId = ref<string | null>(null)
const dragOverId = ref<string | null>(null)
function onBlkDragStart(id: string, e: DragEvent) {
  dragId.value = id; addOpen.value = false
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'
}
function onBlkDragOver(id: string) { if (dragId.value && id !== dragId.value) dragOverId.value = id }
function onBlkDragEnd() { dragId.value = null; dragOverId.value = null }
function onBlkDrop(targetId: string) {
  const from = dragId.value
  dragId.value = null; dragOverId.value = null
  const arr = props.data.blocks
  if (!from || from === targetId || !arr) return
  const fi = arr.findIndex((b) => b.id === from)
  if (fi < 0 || !arr.some((b) => b.id === targetId)) return
  const [moved] = arr.splice(fi, 1)
  arr.splice(arr.findIndex((b) => b.id === targetId), 0, moved) // drop before the target → in-place, persists
}

// ---- outline navigation (left panel): a scroll-to index of the block stack (mirrors Quick access) ----
const outlineOpen = computed({ get: () => !!props.data.outlineOpen, set: (v) => { props.data.outlineOpen = v } })
const bodyEl = ref<HTMLElement | null>(null)
const OUT_GLYPH: Record<BlockType, string> = { section: '▤', heading: 'H', text: '¶', grid: '▦', meta: '≣', divider: '—' }
function blockLabel(b: GalleryBlock): string {
  if (b.type === 'section') return b.title || 'Section'
  if (b.type === 'heading') return b.text || 'Heading'
  if (b.type === 'text') return (b.text.split('\n')[0] || '').slice(0, 42) || 'Text'
  if (b.type === 'grid') return `Grid · ${Array.isArray(b.imageIds) ? b.imageIds.length : 0}`
  if (b.type === 'meta') return 'Metadata'
  return 'Divider'
}
// One entry per navigable block (dividers are structural noise — skipped). Blocks under a section indent.
interface OutlineEntry { id: string; type: BlockType; label: string; glyph: string; depth: 0 | 1 }
const outline = computed<OutlineEntry[]>(() => {
  let inSection = false
  return blocks.value.flatMap((b): OutlineEntry[] => {
    if (b.type === 'section') { inSection = true; return [{ id: b.id, type: b.type, label: blockLabel(b), glyph: OUT_GLYPH[b.type], depth: 0 }] }
    if (b.type === 'divider') return []
    return [{ id: b.id, type: b.type, label: blockLabel(b), glyph: OUT_GLYPH[b.type], depth: inSection ? 1 : 0 }]
  })
})
// Scroll a block to the TOP of the viewport (item 8); if it's folded inside a collapsed section,
// expand that section first.
function scrollToBlock(id: string) {
  if (hiddenIds.value.has(id)) {
    const arr = blocks.value
    for (let i = arr.findIndex((b) => b.id === id); i >= 0; i--) {
      const b = arr[i]
      if (b.type === 'section') { if (b.collapsed) b.collapsed = false; break }
    }
  }
  selectedId.value = id
  nextTick(() => bodyEl.value?.querySelector(`.blk[data-bid="${id}"]`)?.scrollIntoView({ block: 'start', behavior: 'smooth' }))
}

// Outline drag-reorder (item 6): dragging a section carries its member blocks; a normal block moves alone.
const outDragId = ref<string | null>(null)
const outOverId = ref<string | null>(null)
function outSpan(id: string): string[] { // ids that move together when dragging `id`
  const arr = blocks.value
  const i = arr.findIndex((b) => b.id === id)
  if (i < 0) return []
  if (arr[i].type !== 'section') return [id]
  const ids = [id]
  for (let j = i + 1; j < arr.length && arr[j].type !== 'section'; j++) ids.push(arr[j].id)
  return ids
}
function onOutDragStart(id: string, e: DragEvent) { outDragId.value = id; if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move' }
function onOutOver(id: string) { if (outDragId.value && id !== outDragId.value) outOverId.value = id }
function onOutDragEnd() { outDragId.value = null; outOverId.value = null }
function onOutDrop(targetId: string) {
  const from = outDragId.value
  outDragId.value = null; outOverId.value = null
  const arr = props.data.blocks
  if (!from || from === targetId || !arr) return
  const span = outSpan(from)
  if (span.includes(targetId)) return // can't drop a section inside its own body
  const moving = span.map((id) => arr.find((b) => b.id === id)).filter((b): b is GalleryBlock => !!b)
  for (const id of span) { const k = arr.findIndex((b) => b.id === id); if (k >= 0) arr.splice(k, 1) }
  const ti = arr.findIndex((b) => b.id === targetId)
  arr.splice(ti < 0 ? arr.length : ti, 0, ...moving) // drop before the target
}
</script>

<template>
  <div class="gnode" :class="{ selected, readonly, embedded }" @click="addOpen = false; metaScopeOpen = null" @dragover.prevent @drop="onRootDrop">
    <div class="gnhd">
      <template v-if="!embedded">
        <span class="ic">▦</span>
        <span class="ttl">Gallery</span>
        <span v-if="title" class="ctx">· {{ title }}</span>
        <span class="ctx">· {{ total }} image{{ total === 1 ? '' : 's' }}</span>
      </template>
      <span class="hsp"></span>
      <button class="qtoggle nodrag" :class="{ on: outlineOpen }" title="Outline — navigate blocks" @pointerdown.stop @click.stop="outlineOpen = !outlineOpen">
        ☰ Outline
      </button>
      <button class="qtoggle nodrag" :class="{ on: quickOpen }" title="Quick access — unsorted images" @pointerdown.stop @click.stop="quickOpen = !quickOpen">
        ⧉ Quick<span v-if="unassigned.length" class="qbadge">{{ unassigned.length }}</span>
      </button>
      <div class="addwrap">
        <button class="addbtn nodrag" @pointerdown.stop @click.stop="toggleAdd()"><span>＋</span> Add block</button>
        <div v-if="addOpen" class="addmenu nodrag" @pointerdown.stop @click.stop>
          <button v-for="t in ADD_TYPES" :key="t.type" class="amrow" @click.stop="addBlock(t.type)">
            <span class="gl">{{ t.glyph }}</span><span class="aml">{{ t.label }}<small>{{ t.hint }}</small></span>
          </button>
        </div>
      </div>
    </div>

    <div class="gmain">
    <!-- Outline — a scroll-to index of the block stack (left side); navigation counterpart to Quick access -->
    <div v-if="outlineOpen" class="navpanel nowheel">
      <div class="nphd">Outline</div>
      <div v-if="outline.length" class="nplist">
        <button v-for="o in outline" :key="o.id" class="nprow nodrag" :draggable="!readonly"
          :class="['d' + o.depth, { sec: o.type === 'section', selrow: selectedId === o.id, odrag: outDragId === o.id, odrop: outOverId === o.id }]"
          :style="{ '--sec': secColor(o.id) }"
          @pointerdown.stop @click.stop="scrollToBlock(o.id)"
          @dragstart="onOutDragStart(o.id, $event)" @dragend="onOutDragEnd"
          @dragover.prevent="onOutOver(o.id)" @dragleave="outOverId = null" @drop.prevent="onOutDrop(o.id)">
          <span class="npg">{{ o.glyph }}</span><span class="npl">{{ o.label }}</span>
        </button>
      </div>
      <div v-else class="npempty">No blocks yet — add one to build the outline.</div>
    </div>

    <div ref="bodyEl" class="gnbody nowheel" @scroll="addOpen = false">
      <div class="glist">
        <template v-for="b in blocks" :key="b.id">
          <div v-show="!hiddenIds.has(b.id)" class="blk" :data-bid="b.id"
            :class="['blk-' + b.type, { drop: dragOverId === b.id, dragging: dragId === b.id, sel: selectedId === b.id,
              sechead: sectionInfo.get(b.id)?.head, seccollapsed: b.type === 'section' && b.collapsed,
              secempty: sectionInfo.get(b.id)?.head && sectionMemberCount(b.id) === 0,
              insec: sectionInfo.get(b.id)?.member, seclast: sectionInfo.get(b.id)?.last }]"
            :style="{ '--sec': secColor(b.id) }" @click.stop="selectBlock(b.id)"
            @dragover.prevent="onBlkDragOver(b.id)" @drop.prevent="onBlkDrop(b.id)" @dragleave="dragOverId = null">
            <span class="bgrip nodrag" title="Drag to reorder" :draggable="!readonly"
              @pointerdown.stop @dragstart="onBlkDragStart(b.id, $event)" @dragend="onBlkDragEnd">⠿</span>
            <div v-if="b.type !== 'grid'" class="bacts nodrag">
              <button class="del nodrag" title="Delete block" @pointerdown.stop @click.stop="deleteBlock(b.id)">🗑</button>
            </div>

            <!-- section — a collapsible group boundary -->
            <div v-if="b.type === 'section'" class="b-section">
              <button class="ctgl nodrag" :title="b.collapsed ? 'Expand section' : 'Collapse section'" @pointerdown.stop @mousedown.stop @click.stop="b.collapsed = !b.collapsed">
                <span class="chev">{{ b.collapsed ? '▸' : '▾' }}</span>
              </button>
              <input class="secname nodrag" v-model="b.title" placeholder="Section" @pointerdown.stop @mousedown.stop @click.stop />
              <span v-if="b.collapsed && sectionMemberCount(b.id)" class="seccnt">{{ sectionMemberCount(b.id) }} block{{ sectionMemberCount(b.id) === 1 ? '' : 's' }}</span>
            </div>

            <!-- image grid — an album owning its images; a drop target for thumbs/kept drafts -->
            <div v-else-if="b.type === 'grid'" class="b-grid" :data-gid="b.id" :class="{ droptarget: gridDropTarget === b.id }"
              @dragover.prevent="gridDropTarget = b.id" @dragleave="gridDropTarget = null" @drop.prevent.stop="onGridDrop(b, $event)">
              <div class="gridtool nodrag">
                <button class="ctgl nodrag" :title="b.collapsed ? 'Expand grid' : 'Collapse to one row'"
                  @pointerdown.stop @mousedown.stop @click.stop="b.collapsed = !b.collapsed"><span class="chev">{{ b.collapsed ? '▸' : '▾' }}</span></button>
                <div class="cols">
                  <button v-for="n in ([2, 3, 4, 5, 6, 7, 8] as const)" :key="n" class="nodrag" :class="{ on: b.cols === n }"
                    @pointerdown.stop @click.stop="setCols(b, n)">{{ n }}</button>
                </div>
                <span class="gtcount">{{ gridImages(b).length }} image{{ gridImages(b).length === 1 ? '' : 's' }}</span>
                <button v-if="gridImages(b).length" class="gtbtn nodrag" title="Download all images in this grid"
                  @pointerdown.stop @click.stop="emit('download', urlsOf(gridImages(b)))">⤓</button>
                <button class="gtbtn gtdel nodrag" title="Delete this grid" @pointerdown.stop @click.stop="deleteBlock(b.id)">🗑</button>
              </div>
              <div v-if="gridImages(b).length" class="gimgs" :style="{ '--cols': b.cols }">
                <div v-for="(im, i) in gridVisible(b)" :key="im.id" class="gthumb nodrag"
                  :class="{ fav: im.data.favorite, dropbefore: imgDropId === im.id && !imgDropAfter, dropafter: imgDropId === im.id && imgDropAfter }"
                  :style="{ '--ar': im.data.ar || (3 / 4) }" :draggable="!readonly"
                  @dragstart="onThumbDrag(im.id, $event)" @dragend="onThumbDragEnd"
                  @dragover.prevent.stop="onThumbOver(im.id, $event)" @drop.prevent.stop="onThumbDrop(b, im.id, $event)"
                  @pointerdown.stop @click.stop="gridHidden(b) && i === b.cols - 1 ? (b.collapsed = false) : emit('preview', im.data.url || '')">
                  <img class="im" :src="thumbSrc(im.data.url, b.cols)" alt="gallery image" loading="lazy" draggable="false" />
                  <div class="thbar nodrag">
                    <button class="thb dl" title="Download image" @pointerdown.stop @click.stop="emit('download', urlsOf([im]))">⤓</button>
                    <button class="thb toq" title="Move to Quick access" @pointerdown.stop @click.stop="emit('toQuick', im.id)">⇥</button>
                    <button class="thb del" title="Delete image from the work" @pointerdown.stop @click.stop="emit('deleteImg', im.id)">🗑</button>
                  </div>
                  <button class="star nodrag" title="Toggle favourite" @pointerdown.stop @click.stop="emit('favorite', im.id)">★</button>
                  <div v-if="gridHidden(b) && i === b.cols - 1" class="gmore">+{{ gridHidden(b) }}</div>
                </div>
              </div>
              <div v-else class="ghint">Empty album — keep generations here, or drag images in from Quick access.</div>
            </div>

            <!-- heading -->
            <input v-else-if="b.type === 'heading'" class="b-heading nodrag" :class="'h' + b.level"
              v-model="b.text" placeholder="Heading" @pointerdown.stop @mousedown.stop @click.stop="selectBlock(b.id)" />
            <!-- text / description -->
            <textarea v-else-if="b.type === 'text'" class="b-text nodrag nowheel" v-model="b.text"
              placeholder="Write a description…" @pointerdown.stop @mousedown.stop @click.stop="selectBlock(b.id)" @input="autogrow"></textarea>
            <!-- metadata — a properties strip; auto values summarise the bound grid (or whole gallery) -->
            <div v-else-if="b.type === 'meta'" class="b-meta">
              <div class="mscope nodrag">
                <span class="mscl">Summarises</span>
                <div class="msdd">
                  <button class="msbtn nodrag" @pointerdown.stop @click.stop="metaScopeOpen = metaScopeOpen === b.id ? null : b.id">
                    {{ scopeLabel(b) }}<span class="car">▾</span>
                  </button>
                  <div v-if="metaScopeOpen === b.id" class="msmenu nodrag" @pointerdown.stop @click.stop>
                    <button class="msrow" :class="{ on: !b.gridId }" @click.stop="setMetaScope(b, undefined)">Whole gallery</button>
                    <button v-for="g in gridOptions" :key="g.id" class="msrow" :class="{ on: b.gridId === g.id }" @click.stop="setMetaScope(b, g.id)">
                      {{ g.label }}<small>{{ g.count }} image{{ g.count === 1 ? '' : 's' }}</small>
                    </button>
                    <div v-if="!gridOptions.length" class="msempty">No grids yet</div>
                  </div>
                </div>
              </div>
              <div class="metagrid">
                <div v-for="(f, fi) in b.fields" :key="fi" class="mfield">
                  <input v-if="!f.auto" class="fk fk-edit nodrag" v-model="f.key" placeholder="Field" @pointerdown.stop />
                  <div v-else class="fk">{{ f.key }}</div>
                  <div class="fv">
                    <template v-if="f.kind === 'chips'">
                      <span v-for="t in metaChips(b, f).slice(0, TAG_CAP)" :key="t" class="tagc">{{ t }}</span>
                      <span v-if="metaChips(b, f).length > TAG_CAP" class="muted">+{{ metaChips(b, f).length - TAG_CAP }} more</span>
                      <span v-if="!metaChips(b, f).length" class="muted">—</span>
                    </template>
                    <input v-else-if="!f.auto" class="fv-edit nodrag" :value="metaText(b, f)" placeholder="value"
                      @input="f.value = ($event.target as HTMLInputElement).value" @pointerdown.stop />
                    <span v-else :class="{ mono: f.kind === 'mono' }">{{ metaText(b, f) }}</span>
                  </div>
                  <button v-if="!f.auto" class="mrm nodrag" title="Remove field" @pointerdown.stop @click.stop="removeMetaField(b, fi)">✕</button>
                </div>
                <button class="maddfield nodrag" @pointerdown.stop @click.stop="addMetaField(b)">＋ Add field</button>
              </div>
            </div>

            <!-- divider -->
            <div v-else-if="b.type === 'divider'" class="b-divider"><div class="ln"></div></div>
          </div>
        </template>
        <div v-if="!blocks.length" class="glhint">Empty gallery — add a block below to start.</div>
      </div>
    </div>

    <!-- Quick access — the default unsorted bin (right side); drop here to unassign, drag out into grids -->
    <div v-if="quickOpen" class="quickpanel nowheel" :class="{ droptarget: quickDropOver }"
      @dragover.prevent="quickDropOver = true" @dragleave="quickDropOver = false" @drop.prevent.stop="onQuickDrop">
      <div class="qphd">
        <span>Quick access</span>
        <span class="qpn">{{ unassigned.length }}</span>
        <button v-if="unassigned.length" class="qpbtn qpdl nodrag" title="Download all unsorted images"
          @pointerdown.stop @click.stop="emit('download', urlsOf(unassigned))">⤓</button>
        <button v-if="clearableQuick.length" class="qpbtn qpclear nodrag" title="Delete all unsorted images except favourites"
          @pointerdown.stop @click.stop="emit('clearQuick', clearableQuick)">🗑</button>
      </div>
      <div v-if="unassigned.length" class="qpgrid">
        <div v-for="im in unassigned" :key="im.id" class="gthumb qthumb nodrag" :class="{ fav: im.data.favorite }"
          :style="{ '--ar': im.data.ar || (3 / 4) }" :draggable="!readonly" @dragstart="onThumbDrag(im.id, $event)" @dragend="onThumbDragEnd"
          @pointerdown.stop @click.stop="emit('preview', im.data.url || '')">
          <img class="im" :src="thumbSrc(im.data.url, 2)" alt="gallery image" loading="lazy" draggable="false" />
          <div class="thbar nodrag">
            <button class="thb del" title="Delete image from the work" @pointerdown.stop @click.stop="emit('deleteImg', im.id)">🗑</button>
          </div>
          <button class="star nodrag" title="Toggle favourite" @pointerdown.stop @click.stop="emit('favorite', im.id)">★</button>
        </div>
      </div>
      <div v-else class="qphint">Unsorted images land here — kept generations and anything dropped outside a grid. Drag them into a grid to organise.</div>
    </div>
    </div>

    <div class="gnft">
      <span>{{ blocks.length }} block{{ blocks.length === 1 ? '' : 's' }}</span>
      <span v-if="selectedId" class="ftsel">· new block lands after the selected one</span>
      <span class="sp"></span>
    </div>
  </div>
</template>

<style scoped>
.gnode{width:100%;height:100%;display:flex;flex-direction:column;overflow:hidden;border:1.5px solid var(--border-strong);border-radius:12px;
  background:color-mix(in srgb,var(--surface-1) 92%,transparent)}
.gnode.selected{border-color:var(--accent)}
/* Embedded (full-page Works host): no card chrome — the page's top bar already frames the work. */
.gnode.embedded{border:0;border-radius:0;background:transparent}
/* View mode (readonly): hide edit affordances, make inline editors non-interactive. Nav (Outline/Quick,
   collapse toggles), preview, download and favourite-state display stay. */
/* !important: some of these have hover rules (e.g. `.blk:hover .bacts{display:block}`) of equal
   specificity that would otherwise re-show the control on hover in View. */
.gnode.readonly .addwrap,
.gnode.readonly .bgrip,
.gnode.readonly .bacts,
.gnode.readonly .gtdel,
.gnode.readonly .thb.toq,
.gnode.readonly .thb.del,
.gnode.readonly .maddfield,
.gnode.readonly .mrm,
.gnode.readonly .qpclear,
.gnode.readonly .msbtn .car{display:none !important}
.gnode.readonly .secname,
.gnode.readonly .b-heading,
.gnode.readonly .b-text,
.gnode.readonly .fk-edit,
.gnode.readonly .fv-edit,
.gnode.readonly .msbtn,
.gnode.readonly .star{pointer-events:none}
.gnode.readonly .gthumb:not(.fav) .star{display:none}
.gnode.readonly .gthumb{cursor:zoom-in}
.gnhd{display:flex;align-items:center;gap:8px;height:40px;flex-shrink:0;padding:0 12px;border-bottom:1px solid var(--border);background:var(--surface-1)}
.gnhd .ic{color:var(--text-faint);font-size:14px}
.gnhd .ttl{font-weight:650;font-size:13px}
.gnhd .ctx{font-size:12px;color:var(--text-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gnhd .hsp{flex:1}

/* ＋ Add block menu (header + footer) — absolute within the node (its overflow is only clipped at the node edge) */
.addwrap{position:relative}
.addbtn{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);padding:4px 10px;font:inherit;font-size:11.5px;font-weight:600;cursor:pointer;white-space:nowrap}
.addbtn:hover{color:var(--accent);border-color:var(--accent)}
.addmenu{position:absolute;right:0;top:calc(100% + 5px);z-index:30;min-width:190px;border:1px solid var(--border-strong);border-radius:var(--radius-lg);background:var(--surface-1);box-shadow:0 10px 30px rgba(0,0,0,.45);padding:5px}
.addmenu.up{top:auto;bottom:calc(100% + 5px)}
.addmenu .amrow{display:flex;align-items:center;gap:9px;width:100%;border:0;background:transparent;color:var(--text);font:inherit;font-size:12.5px;font-weight:600;padding:7px 9px;border-radius:var(--radius);cursor:pointer;text-align:left}
.addmenu .amrow:hover{background:var(--surface-3)}
.addmenu .amrow .gl{width:20px;height:20px;border-radius:5px;background:var(--surface-3);border:1px solid var(--border);display:inline-flex;align-items:center;justify-content:center;font-size:12px;color:var(--text-dim);flex-shrink:0}
.addmenu .amrow .aml{display:flex;flex-direction:column;line-height:1.25}
.addmenu .amrow .aml small{font-weight:500;color:var(--text-faint);font-size:11px}

/* block wrapper: a hover grip (drag-reorder) on the left, a hover delete on the right */
.blk{position:relative;border-radius:8px;padding:3px 6px 3px 22px}
.blk:hover{background:color-mix(in srgb,var(--surface-2) 45%,transparent)}
.blk-grid:hover,.blk-divider:hover{background:transparent}
.blk.dragging{opacity:.45}
.blk.drop{box-shadow:0 -2px 0 0 var(--accent)}
.bgrip{position:absolute;left:4px;top:8px;width:14px;text-align:center;color:var(--text-faint);font-size:12px;line-height:1;cursor:grab;opacity:0;user-select:none}
.blk:hover .bgrip{opacity:1}
.bgrip:active{cursor:grabbing}
.bacts{position:absolute;right:4px;top:4px;display:none;z-index:4}
.blk:hover .bacts{display:block}
.bacts .del{border:1px solid var(--border);background:var(--surface-1);color:var(--text-faint);border-radius:5px;height:22px;min-width:22px;font-size:11px;cursor:pointer;padding:0 4px}
.bacts .del:hover{color:var(--danger);border-color:var(--danger)}

/* section grouping (item 1): a tinted, colour-coded band brackets a section header + the blocks it owns.
   Consecutive sections get distinct `--sec` hues; members cancel the list gap so the band is continuous. */
.blk.sechead{margin:12px 0 0;padding:1px 8px 0 20px;border:1px solid color-mix(in srgb,var(--sec) 42%,var(--border));border-bottom:0;border-radius:11px 11px 0 0;background:color-mix(in srgb,var(--sec) 10%,transparent)}
.blk.seccollapsed,.blk.secempty{border-bottom:1px solid color-mix(in srgb,var(--sec) 42%,var(--border));border-radius:11px}
.blk.insec{margin:-8px 0 0;padding:3px 8px 3px 20px;border-radius:0;border-left:1px solid color-mix(in srgb,var(--sec) 42%,var(--border));border-right:1px solid color-mix(in srgb,var(--sec) 42%,var(--border));background:color-mix(in srgb,var(--sec) 5%,transparent)}
.blk.insec:hover{background:color-mix(in srgb,var(--sec) 11%,transparent)}
.blk.seclast{border-bottom:1px solid color-mix(in srgb,var(--sec) 42%,var(--border));border-radius:0 0 11px 11px;margin-bottom:6px;padding-bottom:8px}
.blk.sechead .bgrip,.blk.insec .bgrip{left:6px}
.blk.sel{box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--accent) 55%,transparent)}

/* section — a group boundary bar */
.b-section{display:flex;align-items:center;gap:8px;padding:8px 2px 7px;border-bottom:1px solid color-mix(in srgb,var(--sec) 42%,var(--border));margin-top:0}
.b-section .seccnt{font-size:10.5px;color:var(--text-faint);font-weight:600;white-space:nowrap}
/* obvious collapse/expand toggle — a bordered chevron button (item 12), shared by sections + grids */
.ctgl{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;flex-shrink:0;border:1px solid var(--border-strong);border-radius:6px;background:var(--surface-2);color:var(--text-dim);cursor:pointer;padding:0;font-size:11px;line-height:1}
.ctgl:hover{color:var(--accent);border-color:var(--accent)}
.ctgl .chev{pointer-events:none}
.b-section .secname{flex:1;min-width:0;border:0;background:transparent;color:var(--text);font:inherit;font-size:13.5px;font-weight:700;outline:none;padding:2px 4px;border-radius:4px}
.b-section .secname:hover{background:var(--surface-3)}
.b-section .secname:focus{background:var(--surface-1)}
.b-section .secname::placeholder{color:var(--text-faint);font-weight:600}

/* copy blocks — borderless inline editors */
.b-heading{width:100%;border:0;background:transparent;color:var(--text);font:inherit;font-weight:750;outline:none;padding:6px 30px 4px 2px}
.b-heading.h1{font-size:19px;letter-spacing:-.01em}
.b-heading.h2{font-size:15px}
.b-heading::placeholder{color:var(--text-faint)}
.b-text{display:block;width:100%;border:0;background:transparent;color:var(--text-dim);font:inherit;font-size:12.5px;line-height:1.6;outline:none;resize:none;overflow:hidden;min-height:22px;padding:2px 30px 2px 2px}
.b-text::placeholder{color:var(--text-faint)}
.b-divider{padding:9px 2px}
.b-divider .ln{height:1px;background:var(--border)}

/* metadata — a properties strip */
.b-meta{padding-top:2px}
/* scope picker — bind the summary to a grid or the whole gallery (token-styled dropdown, no native select) */
.mscope{display:flex;align-items:center;gap:8px;padding:0 2px 6px}
.mscope .mscl{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.3px;color:var(--text-faint)}
.msdd{position:relative}
.msbtn{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text);border-radius:var(--radius);padding:3px 9px;font:inherit;font-size:11.5px;font-weight:600;cursor:pointer}
.msbtn:hover{border-color:var(--accent)}
.msbtn .car{color:var(--text-faint);font-size:9px}
.msmenu{position:absolute;left:0;top:calc(100% + 4px);z-index:30;min-width:170px;border:1px solid var(--border-strong);border-radius:var(--radius-lg);background:var(--surface-1);box-shadow:0 10px 30px rgba(0,0,0,.45);padding:5px}
.msmenu .msrow{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;border:0;background:transparent;color:var(--text);font:inherit;font-size:12px;font-weight:600;padding:6px 9px;border-radius:var(--radius);cursor:pointer;text-align:left}
.msmenu .msrow:hover{background:var(--surface-3)}
.msmenu .msrow.on{color:var(--accent)}
.msmenu .msrow small{font-weight:500;color:var(--text-faint);font-size:11px}
.msmenu .msempty{padding:6px 9px;font-size:11px;color:var(--text-faint)}
.metagrid{border:1px solid var(--border);border-radius:8px;background:color-mix(in srgb,var(--surface-2) 55%,transparent);overflow:hidden}
.metagrid .mfield{display:grid;grid-template-columns:110px 1fr auto;gap:8px;align-items:center;padding:7px 11px}
.metagrid .mfield + .mfield{border-top:1px solid var(--border)}
.metagrid .fk{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.3px;color:var(--text-faint)}
.metagrid .fk-edit{border:1px solid transparent;background:transparent;border-radius:4px;padding:2px 4px;text-transform:none;letter-spacing:0;font:inherit;font-size:12px;font-weight:600;color:var(--text);outline:none}
.metagrid .fk-edit:hover{border-color:var(--border)}.metagrid .fk-edit:focus{border-color:var(--accent)}
.metagrid .fv{display:flex;flex-wrap:wrap;gap:5px;align-items:center;color:var(--text-dim);font-size:12px;min-width:0}
.metagrid .fv .muted{color:var(--text-faint)}
.metagrid .fv .mono{font-family:ui-monospace,monospace;font-size:11px}
.metagrid .fv-edit{flex:1;min-width:0;border:1px solid var(--border);background:var(--surface-1);border-radius:4px;padding:3px 6px;font:inherit;font-size:12px;color:var(--text);outline:none}
.metagrid .fv-edit:focus{border-color:var(--accent)}
.metagrid .tagc{font-size:10.5px;color:var(--accent);background:var(--nav-active);border:1px solid color-mix(in srgb,var(--accent) 30%,transparent);border-radius:20px;padding:1px 8px}
.metagrid .mrm{border:0;background:transparent;color:var(--text-faint);font-size:12px;cursor:pointer;padding:0 2px}
.metagrid .mrm:hover{color:var(--danger)}
.metagrid .maddfield{width:100%;border:0;border-top:1px solid var(--border);background:transparent;color:var(--text-faint);font:inherit;font-size:11.5px;font-weight:600;text-align:left;padding:7px 11px;cursor:pointer}
.metagrid .maddfield:hover{color:var(--accent)}
.glhint{font-size:12px;color:var(--text-faint);text-align:center;padding:20px}

.gnft{display:flex;align-items:center;gap:8px;height:34px;flex-shrink:0;padding:0 12px;border-top:1px solid var(--border);background:var(--surface-1);font-size:11.5px;color:var(--text-faint)}
.gnft .sp{flex:1}
.gnft .ftsel{color:var(--accent);font-weight:600}

.gmain{flex:1;min-height:0;display:flex}
.gnbody{flex:1;min-width:0;min-height:0;overflow-y:auto;padding:10px 12px 14px}
.glist{display:flex;flex-direction:column;gap:8px}

/* Quick access panel (right) */
.qtoggle{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);padding:4px 9px;font:inherit;font-size:11.5px;font-weight:600;cursor:pointer;white-space:nowrap}
.qtoggle:hover{color:var(--text)}
.qtoggle.on{background:var(--nav-active);color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,var(--border))}
.qbadge{display:inline-flex;align-items:center;justify-content:center;min-width:15px;height:15px;padding:0 4px;border-radius:8px;background:var(--accent);color:var(--on-accent);font-size:9.5px;font-weight:700}
.quickpanel{width:212px;flex-shrink:0;border-left:1px solid var(--border);display:flex;flex-direction:column;overflow-y:auto;background:color-mix(in srgb,var(--surface-2) 40%,transparent)}
.quickpanel.droptarget{outline:2px dashed var(--accent);outline-offset:-3px;background:color-mix(in srgb,var(--accent) 8%,transparent)}
.qphd{display:flex;align-items:center;gap:6px;padding:9px 12px;font-size:10.5px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint);border-bottom:1px solid var(--border);flex-shrink:0}
.qphd .qpn{margin-left:auto;color:var(--text-dim)}
.qpbtn{border:1px solid var(--border);background:var(--surface-2);color:var(--text-faint);border-radius:5px;width:22px;height:20px;font-size:12px;cursor:pointer;padding:0;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.qpdl:hover{color:var(--accent);border-color:var(--accent)}
.qpclear:hover{color:var(--danger);border-color:var(--danger)}
.qpgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;padding:8px}
.qthumb{aspect-ratio:var(--ar,3/4)}
.qphint{padding:16px 12px;font-size:11px;color:var(--text-faint);line-height:1.5}

/* Outline navigation panel (left) */
.navpanel{width:190px;flex-shrink:0;border-right:1px solid var(--border);display:flex;flex-direction:column;overflow-y:auto;background:color-mix(in srgb,var(--surface-2) 40%,transparent)}
.nphd{padding:9px 12px;font-size:10.5px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint);border-bottom:1px solid var(--border);flex-shrink:0}
.nplist{display:flex;flex-direction:column;gap:1px;padding:6px 6px 10px}
.nprow{display:flex;align-items:center;gap:7px;width:100%;border:0;background:transparent;color:var(--text-dim);font:inherit;font-size:12px;text-align:left;padding:5px 8px;border-radius:6px;cursor:pointer;overflow:hidden}
.nprow:hover{background:var(--surface-3);color:var(--text)}
.nprow.sec{font-weight:700;color:var(--text);margin-top:4px;border-left:3px solid var(--sec, transparent);border-radius:0 6px 6px 0;padding-left:6px}
.nprow.d1{padding-left:20px;border-left:3px solid color-mix(in srgb,var(--sec) 45%,transparent);border-radius:0}
.nprow.selrow{background:var(--nav-active);color:var(--accent)}
.nprow.odrag{opacity:.4}
.nprow.odrop{box-shadow:0 -2px 0 0 var(--accent)}
.nprow .npg{width:15px;flex-shrink:0;text-align:center;color:var(--text-faint);font-size:11px}
.nprow.sec .npg{color:var(--text-dim)}
.nprow .npl{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.npempty{padding:16px 12px;font-size:11px;color:var(--text-faint);line-height:1.5}

.b-grid{display:flex;flex-direction:column;border-radius:8px}
.b-grid.droptarget{outline:2px dashed var(--accent);outline-offset:2px;background:color-mix(in srgb,var(--accent) 7%,transparent)}
.gridtool{display:flex;align-items:center;gap:8px;padding:0 1px 8px}
.gmore{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.55);color:#fff;font-size:14px;font-weight:700;cursor:pointer}
.cols{display:inline-flex;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.cols button{border:0;border-left:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);font:inherit;font-size:11px;font-weight:700;padding:4px 8px;cursor:pointer}
.cols button:first-child{border-left:0}
.cols button.on{background:var(--nav-active);color:var(--accent)}
.gtcount{margin-left:auto;font-size:11px;color:var(--text-faint);font-variant-numeric:tabular-nums}
.gtbtn{border:1px solid var(--border);background:var(--surface-2);color:var(--text-faint);border-radius:5px;width:22px;height:20px;font-size:12px;line-height:1;cursor:pointer;padding:0;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.gtbtn:hover{color:var(--accent);border-color:var(--accent)}
.gtbtn.gtdel:hover{color:var(--danger);border-color:var(--danger)}

.gimgs{display:grid;gap:7px;grid-template-columns:repeat(var(--cols,3),1fr)}
.gthumb{position:relative;aspect-ratio:var(--ar,3/4);border-radius:7px;overflow:hidden;border:1px solid var(--border);cursor:zoom-in;background:var(--surface-3)}
.gthumb:hover{border-color:var(--border-strong)}
.gthumb .im{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
/* insertion caret while reordering — a bright bar on the edge where the thumb will land */
.gthumb.dropbefore::after,.gthumb.dropafter::after{content:'';position:absolute;top:0;bottom:0;width:3px;background:var(--accent);z-index:4;box-shadow:0 0 6px var(--accent)}
.gthumb.dropbefore::after{left:0}
.gthumb.dropafter::after{right:0}
.gthumb .star{position:absolute;right:5px;top:5px;border:0;background:transparent;font-size:13px;line-height:1;cursor:pointer;padding:0;
  color:#fff;opacity:0;text-shadow:0 1px 3px rgba(0,0,0,.7)}
.gthumb.fav .star{opacity:1;color:var(--star)}
.gthumb:hover .star{opacity:1}
.gthumb .star:hover{color:var(--star)}
/* thumb action bar (top-left): delete + move-to-quick, revealed on hover */
.gthumb .thbar{position:absolute;left:5px;top:5px;display:flex;gap:4px;opacity:0}
.gthumb:hover .thbar{opacity:1}
.gthumb .thb{width:19px;height:19px;border:0;border-radius:5px;background:rgba(0,0,0,.6);color:#fff;
  font-size:10px;line-height:1;cursor:pointer;padding:0;display:flex;align-items:center;justify-content:center}
.gthumb .thb.del:hover{background:var(--danger)}
.gthumb .thb.toq:hover,.gthumb .thb.dl:hover{background:var(--accent)}

.ghint{font-size:12px;color:var(--text-faint);text-align:center;padding:26px 16px;line-height:1.5;
  border:1px dashed var(--border-strong);border-radius:8px;background:color-mix(in srgb,var(--surface-1) 55%,transparent)}
</style>
