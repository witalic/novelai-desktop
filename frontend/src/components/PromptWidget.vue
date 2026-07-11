<script setup lang="ts">
/* The library-zone widget (design/prompt-widget-mockup.html, rev 5 + review rounds). Two modes
 * over one zone: Quick access — the work's palette, a scrollable list over the zone's (always
 * hidden) child block nodes — and Find in Library — a read-only, race-guarded vault browser
 * showing ALL blocks (pinned ones greyed out). Filters/sort are transient; every palette
 * mutation goes back to CanvasBoard by nodeId. The zone drags by the header only (dragHandle). */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { listBlocks, listCategories, listTags, resolveBlocks, type BlockSort } from '../api'
import { usePromptBrowse } from '../composables/usePromptBrowse'
import { isStale } from '../canvas/palette'
import type { LibraryBlock, PaletteRow, ZoneNode } from '../types'

const props = defineProps<{
  data: ZoneNode['data']
  selected: boolean
  pins: PaletteRow[] // palette rows in order (view-model over the zone's child nodes)
  pinnedIds: string[]
  revalidate: number // bumped by CanvasBoard when the Library may have changed (re-check versions)
}>()
const emit = defineEmits<{
  toggle: []
  'open-library': []
  'open-settings': []
  pin: [LibraryBlock]
  'pin-many': [LibraryBlock[]] // "Pin all" — every filtered block not already pinned
  use: [LibraryBlock] // ⇢ from browse — copy straight into the station lane, no pinning
  'new-block': []
  copy: [string] // ⇢ from the palette — an independent copy into the station lane (nodeId)
  unpin: [string]
  save: [string] // ↥ → Save to Library… (local rows only)
  patch: [{ nodeId: string; patch: Record<string, unknown> }]
  reorder: [{ nodeId: string; beforeId: string | null }]
}>()

const mode = ref<'stash' | 'browse'>('stash')
const {
  search, selectedCats, sort, tags, items, total, categories, tagOptions, loading, noVault,
  activate, refreshFilters, setSearch, toggleCategory, setSort, toggleTag, clearFilters, loadMore, dispose,
} = usePromptBrowse({ listBlocks, listCategories, listTags })

watch(mode, (m) => { if (m === 'browse') activate() })
// Vault categories load on mount so both modes can show real names/colors (custom categories
// have opaque cat-<uuid> slugs — never surface those raw).
onMounted(() => { refreshFilters(); refreshDrift() })

// ---- version drift: a pinned copy is frozen; its Library block may have moved on ----
const latestById = ref<Record<string, LibraryBlock>>({})
async function refreshDrift() {
  const ids = [...new Set(props.pins.map((p) => p.block_id).filter((x): x is string => !!x))]
  if (!ids.length) { latestById.value = {}; return }
  try {
    latestById.value = Object.fromEntries((await resolveBlocks(ids)).map((b) => [b.id, b]))
  } catch { /* keep the last known versions */ }
}
// Re-check when the set of linked pins changes (a new pin, an unpin, a save-to-library link) and
// when CanvasBoard signals the Library may have changed (returning to Generate, opening a work) —
// the widget stays mounted under KeepAlive, so it can't wait for onMounted to fire again.
watch(() => props.pins.map((p) => p.block_id || '').join(','), refreshDrift)
watch(() => props.revalidate, () => { refreshFilters(); refreshDrift() })
function driftVersion(p: PaletteRow): number | null {
  if (!p.block_id) return null
  const latest = latestById.value[p.block_id]
  return latest && isStale(p.version, latest.version) ? (latest.version ?? 1) : null
}
// Explicit re-freeze: adopt the Library block's current content + version into the pin.
function updatePin(p: PaletteRow) {
  const latest = p.block_id ? latestById.value[p.block_id] : undefined
  if (!latest) return
  emit('patch', { nodeId: p.nodeId, patch: {
    name: latest.name, text: latest.text, polarity: latest.polarity,
    category: latest.category, tags: [...latest.tags], version: latest.version ?? 1,
  } })
}
const catColor = (slug: string) => categories.value.find((c) => c.slug === slug)?.color || '#738496'
const catName = (slug: string) => categories.value.find((c) => c.slug === slug)?.name
  || (slug.startsWith('cat-') ? 'custom' : slug)

// Presses on interactive elements must not select/drag the zone, but empty widget areas should
// keep behaving like any node — so propagation stops only when the press started on a control.
// (Vue Flow selects on `click`, drags from `pointerdown` — the same guard covers all three.)
function stopIfInteractive(e: Event) {
  const t = e.target as HTMLElement | null
  if (t && t !== e.currentTarget && t.closest('button, input, textarea, select, a, .brow')) e.stopPropagation()
}

// Horizontal chip rows scroll with the wheel (inside a canvas node the wheel would zoom otherwise).
function hwheel(e: WheelEvent) {
  const el = e.currentTarget as HTMLElement
  if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
    el.scrollLeft += e.deltaY
    e.preventDefault()
  }
}

// '/' while the canvas has focus → jump into the browse search (CanvasBoard dispatches the event).
const searchEl = ref<HTMLInputElement | null>(null)
function focusSearch() {
  if (props.data.collapsed) return
  mode.value = 'browse'
  requestAnimationFrame(() => searchEl.value?.focus())
}
window.addEventListener('nai:widget-search', focusSearch)

/* ============================ Quick access (palette) ============================ */

// Transient filters + sort over the pinned set. 'pinned' keeps the manual order (≈ pin date);
// drag-reorder is only meaningful there, so rows drag out only in that sort.
const stSearch = ref('')
const stCats = ref<string[]>([])
const stTags = ref<string[]>([])
const stSort = ref<'pinned' | 'category'>('pinned')
const stSortOpen = ref(false)
const stSortLabel = computed(() => (stSort.value === 'category' ? 'By category' : 'Pinned order'))
const stCatOpen = ref(false)
const stashCats = computed(() => {
  const counts: Record<string, number> = {}
  for (const p of props.pins) counts[p.category] = (counts[p.category] || 0) + 1
  return Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([slug, count]) => ({ slug, count }))
})
const stashTagOptions = computed(() => {
  const counts: Record<string, number> = {}
  for (const p of props.pins) for (const t of p.tags) counts[t] = (counts[t] || 0) + 1
  return Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count }))
})
function toggleStCat(slug: string) {
  if (!slug) { stCats.value = []; return } // 'All' resets
  const i = stCats.value.indexOf(slug)
  if (i >= 0) stCats.value.splice(i, 1)
  else stCats.value.push(slug)
}
const shownPins = computed(() => {
  const q = stSearch.value.trim().toLowerCase()
  const filtered = props.pins.filter((p) =>
    (!stCats.value.length || stCats.value.includes(p.category))
    && (!q || `${p.name} ${p.text}`.toLowerCase().includes(q))
    && stTags.value.every((t) => p.tags.includes(t)))
  if (stSort.value === 'category') {
    // by display name (not slug — custom slugs are cat-<uuid> and would scatter)
    return filtered.slice().sort((a, b) => catName(a.category).localeCompare(catName(b.category)) || a.name.localeCompare(b.name))
  }
  return filtered
})
function toggleStTag(name: string) {
  const i = stTags.value.indexOf(name)
  if (i >= 0) stTags.value.splice(i, 1)
  else stTags.value.push(name)
}
function clearStFilters() {
  stSearch.value = ''
  stCats.value = []
  stTags.value = []
}
const stTagOpen = ref(false)
const stTagQuery = ref('')
const stTagMatches = computed(() => {
  const q = stTagQuery.value.trim().toLowerCase()
  const chosen = new Set(stTags.value)
  return stashTagOptions.value.filter((t) => !chosen.has(t.name) && (!q || t.name.toLowerCase().includes(q))).slice(0, 12)
})
function addStTag(name: string) {
  stTagQuery.value = ''
  stTagOpen.value = false
  toggleStTag(name)
}

// Row expansion: local customs edit inline; library-linked pins are frozen copies — view-only
// (edit the independent copies in the work area instead).
const expandedIds = ref<Set<string>>(new Set())
function toggleRow(nodeId: string) {
  const next = new Set(expandedIds.value)
  if (next.has(nodeId)) next.delete(nodeId)
  else next.add(nodeId)
  expandedIds.value = next
}
const renamingId = ref<string | null>(null)
function startRename(nodeId: string) {
  renamingId.value = nodeId
  requestAnimationFrame(() => {
    const el = document.getElementById(`pwname-${nodeId}`) as HTMLInputElement | null
    el?.focus()
    el?.select()
  })
}

// ＋ New custom block: created by CanvasBoard; expand + rename the row as soon as it appears.
let pendingNew = false
function newBlock() {
  pendingNew = true
  emit('new-block')
}
watch(() => props.pins.length, (now, before) => {
  if (!pendingNew || now <= (before ?? 0)) return
  pendingNew = false
  clearStFilters() // the fresh block must be visible
  stSort.value = 'pinned'
  const row = props.pins.at(0) // new custom blocks prepend — the newest is first
  if (!row) return
  expandedIds.value = new Set(expandedIds.value).add(row.nodeId)
  nextTick(() => startRename(row.nodeId))
})

// Drag: out of the list = an independent copy on the canvas (CanvasBoard's drop handler);
// within the list = reorder (the list's own drop handler stops propagation).
const dragId = ref<string | null>(null)
const dropBeforeId = ref<string | null | undefined>(undefined) // undefined = no marker
function onRowDragStart(e: DragEvent, row: PaletteRow) {
  if (!e.dataTransfer) return
  dragId.value = row.nodeId
  e.dataTransfer.effectAllowed = 'copyMove'
  e.dataTransfer.setData('text/plain', `nai-palette:${row.nodeId}`)
}
function onRowDragOver(e: DragEvent, row: PaletteRow) {
  if (!dragId.value || dragId.value === row.nodeId) return
  e.preventDefault()
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const before = e.clientY < r.top + r.height / 2
  const idx = shownPins.value.findIndex((p) => p.nodeId === row.nodeId)
  dropBeforeId.value = before ? row.nodeId : (shownPins.value[idx + 1]?.nodeId ?? null)
}
function onListDrop(e: DragEvent) {
  if (!dragId.value || dropBeforeId.value === undefined) return
  e.preventDefault()
  e.stopPropagation() // don't let the canvas drop handler spawn a copy
  emit('reorder', { nodeId: dragId.value, beforeId: dropBeforeId.value })
  dragId.value = null
  dropBeforeId.value = undefined
}
function onRowDragEnd() {
  dragId.value = null
  dropBeforeId.value = undefined
}

/* ============================ Find in Library (browse) ============================ */

const catOpen = ref(false)
const catQuery = ref('')
const catLabel = computed(() => {
  if (!selectedCats.value.length) return 'All categories'
  if (selectedCats.value.length === 1) return catName(selectedCats.value[0])
  return `${selectedCats.value.length} categories`
})
const allCount = computed(() => categories.value.reduce((n, c) => n + c.count, 0))
const catMatches = computed(() => {
  const q = catQuery.value.trim().toLowerCase()
  return categories.value.filter((c) => !q || c.name.toLowerCase().includes(q))
})
const browseCatChips = computed(() => categories.value.slice().sort((a, b) => b.count - a.count).slice(0, 8))

const browseSortOpen = ref(false)
const BROWSE_SORTS: { v: BlockSort; l: string }[] = [
  { v: 'updated', l: 'Recently updated' },
  { v: 'created', l: 'Date created' },
  { v: 'category', l: 'By category' },
]
const browseSortLabel = computed(() => BROWSE_SORTS.find((s) => s.v === sort.value)?.l ?? 'Sort')

const tagOpen = ref(false)
const tagQuery = ref('')
const tagMatches = computed(() => {
  const q = tagQuery.value.trim().toLowerCase()
  const chosen = new Set(tags.value)
  return tagOptions.value.filter((t) => !chosen.has(t.name) && (!q || t.name.toLowerCase().includes(q))).slice(0, 12)
})
function addTag(name: string) {
  tagQuery.value = ''
  tagOpen.value = false
  toggleTag(name)
}

// Close the dropdown panels on any press outside them. Capture phase is essential: the widget's
// own handlers stop propagation, which would starve a bubbling document listener.
const rootEl = ref<HTMLElement | null>(null)
function onDocPointer(e: MouseEvent) {
  const t = e.target as HTMLElement | null
  if (!t || !t.closest('.catdd, .tagadd')) {
    catOpen.value = false
    tagOpen.value = false
    stCatOpen.value = false
    stTagOpen.value = false
    stSortOpen.value = false
    browseSortOpen.value = false
  }
}
document.addEventListener('mousedown', onDocPointer, true)
onUnmounted(() => {
  window.removeEventListener('nai:widget-search', focusSearch)
  document.removeEventListener('mousedown', onDocPointer, true)
  dispose()
})

// Pin → CanvasBoard appends a palette node; the row shows its ✓ for a beat before greying out.
const justPinned = ref<Record<string, boolean>>({})
function pin(b: LibraryBlock) {
  emit('pin', b)
  justPinned.value = { ...justPinned.value, [b.id]: true }
  setTimeout(() => {
    const { [b.id]: _, ...rest } = justPinned.value
    void _
    justPinned.value = rest
  }, 900)
}
const pinnedSet = computed(() => {
  const s = new Set(props.pinnedIds)
  for (const id of Object.keys(justPinned.value)) s.delete(id)
  return s
})

// Browse rows: view-only expansion + drag-out (an independent copy, no pinning).
const expandedLib = ref<Set<string>>(new Set())
function toggleLib(id: string) {
  const next = new Set(expandedLib.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedLib.value = next
}
function onLibDragStart(e: DragEvent, b: LibraryBlock) {
  if (!e.dataTransfer) return
  e.dataTransfer.effectAllowed = 'copy'
  e.dataTransfer.setData('text/plain', `nai-libblock:${JSON.stringify(b)}`)
}

// A greyed "Pinned ✓" row clicked → jump to the palette row and flash it.
const flashId = ref<string | null>(null)
function revealPinned(b: LibraryBlock) {
  mode.value = 'stash'
  clearStFilters()
  const row = props.pins.find((p) => p.block_id === b.id)
  if (!row) return
  flashId.value = row.nodeId
  nextTick(() => document.getElementById(`pwrow-${row.nodeId}`)?.scrollIntoView({ block: 'nearest' }))
  setTimeout(() => { flashId.value = null }, 1200)
}

function onListScroll(e: Event) {
  const el = e.target as HTMLElement
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 80) loadMore()
}

// Pin every filtered block (not just the loaded page) that isn't already pinned — pages through
// to the backend cap so a large filtered set comes across whole.
const pinAllBusy = ref(false)
const hasUnpinned = computed(() => items.value.some((b) => !pinnedSet.value.has(b.id)))
async function pinAll() {
  if (pinAllBusy.value) return
  pinAllBusy.value = true
  try {
    const per = 200
    let all: LibraryBlock[] = []
    for (let p = 1; ; p++) {
      const res = await listBlocks({ categories: selectedCats.value, tags: tags.value, sort: sort.value,
        search: search.value.trim() || undefined, page: p, perPage: per })
      all = all.concat(res.items)
      if (all.length >= res.total || res.items.length < per) break
    }
    const seen = new Set(props.pinnedIds)
    const fresh = all.filter((b) => !seen.has(b.id))
    if (fresh.length) emit('pin-many', fresh)
  } finally {
    pinAllBusy.value = false
  }
}
</script>

<template>
  <div ref="rootEl" class="pwidget" :class="{ selected, collapsed: data.collapsed }">
    <div class="pwhd">
      <span class="picon">✦</span>
      <span class="ptitle">Prompt blocks</span>
      <span class="anchor-tag">anchor</span>
      <button class="pcollapse nodrag" :title="data.collapsed ? 'Expand' : 'Collapse to header'"
        @pointerdown.stop @mousedown.stop @click.stop="$emit('toggle')">{{ data.collapsed ? '▸' : '▾' }}</button>
    </div>

    <!-- v-show, not v-if: collapsing must keep the DOM (scroll positions, panel state) alive -->
    <div v-show="!data.collapsed" class="pwmain">
      <div class="pwmode nodrag" @pointerdown="stopIfInteractive" @mousedown="stopIfInteractive" @click="stopIfInteractive">
        <button :class="{ active: mode === 'stash' }" @click="mode = 'stash'">Quick access <span class="n">{{ pins.length }}</span></button>
        <button :class="{ active: mode === 'browse' }" @click="mode = 'browse'">Find in Library</button>
      </div>

      <!-- ================= Quick access ================= -->
      <template v-if="mode === 'stash'">
        <div v-if="pins.length" class="pwtools nodrag" @pointerdown="stopIfInteractive" @mousedown="stopIfInteractive" @click="stopIfInteractive">
          <div class="srow">
            <div class="pwsearch">
              <span class="ic">⌕</span>
              <input v-model="stSearch" type="text" placeholder="Filter pinned…" />
            </div>
            <div class="catdd sortdd">
              <button class="catbtn" title="Sort pinned blocks" @click="stSortOpen = !stSortOpen">
                <span class="lbl">{{ stSortLabel }}</span><span class="car">▾</span>
              </button>
              <div v-if="stSortOpen" class="catpanel nowheel">
                <button class="catitem" :class="{ on: stSort === 'pinned' }" @click="stSort = 'pinned'; stSortOpen = false">Pinned order</button>
                <button class="catitem" :class="{ on: stSort === 'category' }" @click="stSort = 'category'; stSortOpen = false">By category</button>
              </div>
            </div>
          </div>
          <div class="catdd">
            <button class="catbtn" @click="stCatOpen = !stCatOpen">
              <span v-if="stCats.length === 1" class="cdot" :style="{ background: catColor(stCats[0]) }"></span>
              <span class="lbl">{{ !stCats.length ? 'All categories' : stCats.length === 1 ? catName(stCats[0]) : `${stCats.length} categories` }}</span>
              <span class="car">▾</span>
            </button>
            <div v-if="stCatOpen" class="catpanel nowheel">
              <button class="catitem" :class="{ on: !stCats.length }" @click="toggleStCat('')">
                All categories <span class="n">{{ pins.length }}</span>
              </button>
              <button v-for="c in stashCats" :key="c.slug" class="catitem" :class="{ on: stCats.includes(c.slug) }"
                @click="toggleStCat(c.slug)">
                <span class="ck">{{ stCats.includes(c.slug) ? '✓' : '' }}</span>
                <span class="cdot" :style="{ background: catColor(c.slug) }"></span>{{ catName(c.slug) }} <span class="n">{{ c.count }}</span>
              </button>
            </div>
          </div>
          <div class="chiprow nowheel" @wheel.stop="hwheel">
            <button class="chip" :class="{ on: !stCats.length }" @click="toggleStCat('')">All <span class="n">{{ pins.length }}</span></button>
            <button v-for="c in stashCats" :key="c.slug" class="chip" :class="{ on: stCats.includes(c.slug) }"
              @click="toggleStCat(c.slug)">
              <span class="cdot" :style="{ background: catColor(c.slug) }"></span>{{ catName(c.slug) }} <span class="n">{{ c.count }}</span>
            </button>
          </div>
          <div class="tagrow">
            <span class="tglbl">Tags</span>
            <span v-for="t in stTags" :key="t" class="ftag">{{ t }} <button title="Remove" @click="toggleStTag(t)">✕</button></span>
            <span class="tagadd">
              <button v-if="!stTagOpen" class="addtag" @click="stTagOpen = true">+ filter</button>
              <input v-else v-model="stTagQuery" type="text" placeholder="tag…" @keyup.esc="stTagOpen = false" />
              <div v-if="stTagOpen && stTagMatches.length" class="tagpanel nowheel">
                <button v-for="t in stTagMatches" :key="t.name" @click="addStTag(t.name)">{{ t.name }} <span class="n">{{ t.count }}</span></button>
              </div>
            </span>
          </div>
        </div>

        <div class="pwlist nodrag nowheel" @pointerdown="stopIfInteractive" @mousedown="stopIfInteractive"
          @click="stopIfInteractive" @drop="onListDrop" @dragover.prevent>
          <div v-if="!pins.length" class="pwhint">
            Nothing pinned yet.<br />
            <button class="plink" @click="mode = 'browse'">Find in Library</button>
            <span class="psep">·</span>
            <button class="plink" @click="newBlock">＋ New block</button>
          </div>
          <div v-else-if="!shownPins.length" class="pwhint">
            No pinned blocks match.<br />
            <button class="plink" @click="clearStFilters">Clear filters</button>
          </div>
          <template v-else>
            <button class="newrow" title="Add a custom block — lives in this work until you save it to the Library"
              @click="newBlock">＋ New custom block</button>
            <div v-for="p in shownPins" :id="`pwrow-${p.nodeId}`" :key="p.nodeId" class="brow"
              :class="{ dropbefore: dropBeforeId === p.nodeId, flash: flashId === p.nodeId }"
              :style="{ '--cat': catColor(p.category) }" :draggable="stSort === 'pinned'"
              @dragstart="onRowDragStart($event, p)" @dragover="onRowDragOver($event, p)" @dragend="onRowDragEnd">
              <div class="r1">
                <span v-if="stSort === 'pinned'" class="grip">⠿</span>
                <input v-if="renamingId === p.nodeId" :id="`pwname-${p.nodeId}`" class="bnamein" :value="p.name"
                  @input="$emit('patch', { nodeId: p.nodeId, patch: { name: ($event.target as HTMLInputElement).value } })"
                  @blur="renamingId = null" @keyup.enter="renamingId = null" @keyup.esc="renamingId = null" />
                <span v-else class="bname" title="Double-click to rename"
                  @dblclick.stop="startRename(p.nodeId)">{{ p.name || 'Untitled' }}</span>
                <span v-if="!p.block_id" class="lbadge" title="Lives in this work only — save it to the Library to reuse elsewhere">local</span>
                <button v-if="driftVersion(p)" class="updbadge" :title="`Library block is at v${driftVersion(p)} — click to update this pin (re-freeze)`"
                  @click="updatePin(p)">↻ v{{ driftVersion(p) }}</button>
                <span class="pol" :class="{ neg: p.polarity === 'negative' }">{{ p.polarity === 'negative' ? '−' : '＋' }}</span>
                <button class="ricon act" :title="`Copy into the ${p.polarity === 'negative' ? 'negative' : 'positive'} lane`"
                  @click="$emit('copy', p.nodeId)">⇢</button>
                <button class="ricon act" :title="expandedIds.has(p.nodeId) ? 'Collapse' : 'Expand to edit'"
                  @click="toggleRow(p.nodeId)">{{ expandedIds.has(p.nodeId) ? '▾' : '▸' }}</button>
                <button v-if="!p.block_id" class="ricon act save" title="Save to Library…" @click="$emit('save', p.nodeId)">↥</button>
                <button class="ricon act x" title="Unpin from quick access" @click="$emit('unpin', p.nodeId)">✕</button>
              </div>
              <!-- Prompt and tags are separate: the prompt is the generative payload (editable — a
                   pin is a decoupled copy, so this never touches the vault block or past snapshots);
                   the tags are curation metadata that flows to generated images and gallery filters. -->
              <template v-if="expandedIds.has(p.nodeId)">
                <div class="explbl">Prompt</div>
                <textarea class="btextarea nowheel" :value="p.text" placeholder="tags…"
                  @input="$emit('patch', { nodeId: p.nodeId, patch: { text: ($event.target as HTMLTextAreaElement).value } })"></textarea>
                <div class="explbl">Gallery tags</div>
                <div v-if="p.tags.length" class="exptags"><span v-for="t in p.tags" :key="t">{{ t }}</span></div>
                <div v-else class="exphint">No tags — add some in the Library editor.</div>
              </template>
              <div v-else class="btext">{{ p.text || 'empty' }}</div>
            </div>
          </template>
        </div>
        <div class="pwfoot" @pointerdown="stopIfInteractive" @mousedown="stopIfInteractive" @click="stopIfInteractive">
          <span>{{ shownPins.length === pins.length ? `${pins.length} pinned` : `${shownPins.length} of ${pins.length} pinned` }}</span>
          <a class="plib nodrag" @click.stop="mode = 'browse'">Find in Library ⌕</a>
        </div>
      </template>

      <!-- ================= Find in Library ================= -->
      <template v-else>
        <div class="pwtools nodrag" @pointerdown="stopIfInteractive" @mousedown="stopIfInteractive" @click="stopIfInteractive">
          <div class="srow">
            <div class="pwsearch">
              <span class="ic">⌕</span>
              <input ref="searchEl" type="text" placeholder="Search blocks…  ( / )" :value="search"
                @input="setSearch(($event.target as HTMLInputElement).value)" />
            </div>
            <div class="catdd sortdd">
              <button class="catbtn" title="Sort blocks" @click="browseSortOpen = !browseSortOpen">
                <span class="lbl">{{ browseSortLabel }}</span><span class="car">▾</span>
              </button>
              <div v-if="browseSortOpen" class="catpanel nowheel">
                <button v-for="s in BROWSE_SORTS" :key="s.v" class="catitem" :class="{ on: sort === s.v }"
                  @click="setSort(s.v); browseSortOpen = false">{{ s.l }}</button>
              </div>
            </div>
            <button v-if="hasUnpinned" class="pinallbtn" :disabled="pinAllBusy"
              title="Pin every filtered block into Quick access" @click="pinAll">＋ Pin all</button>
          </div>
          <div class="catdd">
            <button class="catbtn" @click="catOpen = !catOpen">
              <span v-if="selectedCats.length === 1" class="cdot" :style="{ background: catColor(selectedCats[0]) }"></span>
              <span class="lbl">{{ catLabel }}</span>
              <span class="car">▾</span>
            </button>
            <div v-if="catOpen" class="catpanel nowheel">
              <input v-if="categories.length > 8" v-model="catQuery" type="text" placeholder="Filter categories…" />
              <button class="catitem" :class="{ on: !selectedCats.length }" @click="toggleCategory('')">
                All categories <span class="n">{{ allCount }}</span>
              </button>
              <button v-for="c in catMatches" :key="c.slug" class="catitem" :class="{ on: selectedCats.includes(c.slug) }"
                @click="toggleCategory(c.slug)">
                <span class="ck">{{ selectedCats.includes(c.slug) ? '✓' : '' }}</span>
                <span class="cdot" :style="{ background: c.color }"></span>{{ c.name }} <span class="n">{{ c.count }}</span>
              </button>
            </div>
          </div>
          <div class="chiprow nowheel" @wheel.stop="hwheel">
            <button class="chip" :class="{ on: !selectedCats.length }" @click="toggleCategory('')">All <span class="n">{{ allCount }}</span></button>
            <button v-for="c in browseCatChips" :key="c.slug" class="chip" :class="{ on: selectedCats.includes(c.slug) }"
              @click="toggleCategory(c.slug)">
              <span class="cdot" :style="{ background: c.color }"></span>{{ c.name }} <span class="n">{{ c.count }}</span>
            </button>
          </div>
          <div class="tagrow">
            <span class="tglbl">Tags</span>
            <span v-for="t in tags" :key="t" class="ftag">{{ t }} <button title="Remove" @click="toggleTag(t)">✕</button></span>
            <span class="tagadd">
              <button v-if="!tagOpen" class="addtag" @click="tagOpen = true">+ filter</button>
              <input v-else v-model="tagQuery" type="text" placeholder="tag…" @keyup.esc="tagOpen = false" />
              <div v-if="tagOpen && tagMatches.length" class="tagpanel nowheel">
                <button v-for="t in tagMatches" :key="t.name" @click="addTag(t.name)">{{ t.name }} <span class="n">{{ t.count }}</span></button>
              </div>
            </span>
          </div>
        </div>

        <div class="pwlist nodrag nowheel" @pointerdown="stopIfInteractive" @mousedown="stopIfInteractive"
          @click="stopIfInteractive" @scroll="onListScroll">
          <template v-if="noVault">
            <div class="pwhint">Connect a vault to browse your prompt blocks.<br />
              <button class="plink" @click="$emit('open-settings')">Open Settings</button>
            </div>
          </template>
          <template v-else-if="loading && !items.length">
            <div v-for="i in 3" :key="i" class="skel"></div>
          </template>
          <template v-else-if="!items.length">
            <div class="pwhint">No blocks match.<br />
              <button class="plink" @click="clearFilters()">Clear filters</button>
            </div>
          </template>
          <template v-else>
            <!-- every block shows; pinned ones grey out (click = jump to the palette row) -->
            <div v-for="b in items" :key="b.id" class="brow" :class="{ pinned: pinnedSet.has(b.id) }"
              :style="{ '--cat': catColor(b.category) }" :draggable="!pinnedSet.has(b.id)"
              @dragstart="onLibDragStart($event, b)" @click="pinnedSet.has(b.id) && revealPinned(b)">
              <div class="r1">
                <span class="bname">{{ b.name }}</span>
                <span v-if="pinnedSet.has(b.id)" class="pinchip">Pinned ✓</span>
                <span class="pol" :class="{ neg: b.polarity === 'negative' }">{{ b.polarity === 'negative' ? '−' : '＋' }}</span>
                <template v-if="!pinnedSet.has(b.id)">
                  <button class="ricon act pinbtn" :class="{ done: justPinned[b.id] }" title="Pin into quick access"
                    @click="pin(b)">{{ justPinned[b.id] ? '✓' : 'Pin' }}</button>
                  <button class="ricon act" :title="`Copy into the ${b.polarity === 'negative' ? 'negative' : 'positive'} lane`"
                    @click="$emit('use', b)">⇢</button>
                  <button class="ricon act" :title="expandedLib.has(b.id) ? 'Collapse' : 'View prompt'"
                    @click="toggleLib(b.id)">{{ expandedLib.has(b.id) ? '▾' : '▸' }}</button>
                </template>
              </div>
              <template v-if="expandedLib.has(b.id)">
                <div class="explbl">Prompt</div>
                <div class="btextview">{{ b.text }}</div>
                <template v-if="b.tags.length">
                  <div class="explbl">Gallery tags</div>
                  <div class="exptags"><span v-for="t in b.tags" :key="t">{{ t }}</span></div>
                </template>
              </template>
              <div v-else class="btext">{{ b.text }}</div>
            </div>
          </template>
        </div>
        <div class="pwfoot" @pointerdown="stopIfInteractive" @mousedown="stopIfInteractive" @click="stopIfInteractive">
          <span>{{ items.length }} of {{ total }} blocks</span>
          <a class="plib nodrag" @click.stop="$emit('open-library')">Open Library ↗</a>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.pwidget{position:relative;width:100%;height:100%;display:flex;flex-direction:column;border:1.5px solid var(--border-strong);
  border-radius:12px;overflow:hidden;background:color-mix(in srgb,var(--surface-1) 92%,transparent)}
.pwidget.selected{border-color:var(--accent)}
.pwhd{display:flex;align-items:center;gap:8px;height:38px;flex-shrink:0;padding:0 12px;
  border-bottom:1px solid var(--border);background:var(--surface-1);font-weight:600;font-size:13px;cursor:grab}
.pwidget.collapsed .pwhd{border-bottom:0}
.picon{font-style:normal}
.ptitle{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.anchor-tag{margin-left:auto;font-size:10px;font-weight:600;color:var(--accent);
  background:color-mix(in srgb,var(--accent) 16%,transparent);padding:1px 7px;border-radius:20px}
.pcollapse{border:0;background:transparent;color:var(--text-faint);cursor:pointer;font-size:11px;padding:2px 4px}
.pcollapse:hover{color:var(--text)}
.pwmain{display:flex;flex-direction:column;flex:1;min-height:0}

.pwmode{display:flex;flex-shrink:0;margin:8px 10px 0;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.pwmode button{flex:1;border:0;border-left:1px solid var(--border);background:var(--surface-1);color:var(--text-dim);
  font:inherit;font-size:11.5px;font-weight:600;padding:6px 4px;cursor:pointer}
.pwmode button:first-child{border-left:0}
.pwmode button.active{background:var(--nav-active);color:var(--accent)}
.pwmode .n{color:var(--text-faint);font-weight:500;font-variant-numeric:tabular-nums}
.pwmode button.active .n{color:var(--accent)}

.pwtools{display:flex;flex-direction:column;gap:8px;padding:10px 10px 8px;border-bottom:1px solid var(--border);flex-shrink:0}
.srow{display:flex;gap:6px;flex-wrap:wrap}
.pinallbtn{flex-shrink:0;display:inline-flex;align-items:center;border:1px solid var(--border-strong);background:var(--surface-2);
  color:var(--text-dim);border-radius:var(--radius);padding:6px 10px;font:inherit;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap}
.pinallbtn:hover{color:var(--accent);border-color:var(--accent)}
.pinallbtn:disabled{opacity:.55;cursor:default}
.pwsearch{position:relative;flex:1;min-width:0}
.pwsearch input{width:100%;font:inherit;font-size:12px;color:var(--text);background:var(--surface-2);
  border:1px solid var(--border);border-radius:var(--radius);padding:6px 8px 6px 26px;outline:none}
.pwsearch input:focus{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 30%,transparent)}
.pwsearch .ic{position:absolute;left:8px;top:50%;transform:translateY(-50%);color:var(--text-faint);font-size:12px;pointer-events:none}
.sortdd{flex-shrink:0;width:132px}
.sortdd .catbtn{font-weight:600}

.chiprow{display:flex;gap:5px;overflow-x:auto;padding-bottom:2px;scrollbar-width:none;min-width:0}
.chiprow::-webkit-scrollbar{display:none}
.chip{flex-shrink:0;display:inline-flex;align-items:center;gap:5px;border:1px solid var(--border);
  background:var(--surface-2);color:var(--text-dim);border-radius:20px;padding:3px 9px;font-size:11px;
  font-weight:600;cursor:pointer;white-space:nowrap}
.chip:hover{border-color:var(--border-strong);color:var(--text)}
.chip.on{background:var(--nav-active);border-color:color-mix(in srgb,var(--accent) 45%,var(--border));color:var(--accent)}
.chip .n{color:var(--text-faint);font-weight:500;font-variant-numeric:tabular-nums}
.chip.on .n{color:var(--accent)}
.cdot{width:8px;height:8px;border-radius:50%;flex-shrink:0}

.catdd{position:relative}
.catbtn{width:100%;display:flex;align-items:center;gap:6px;border:1px solid var(--border);border-radius:var(--radius);
  background:var(--surface-2);color:var(--text);font:inherit;font-size:12px;font-weight:600;padding:6px 8px;cursor:pointer}
.catbtn .lbl{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.catbtn .car{margin-left:auto;color:var(--text-faint);font-size:10px}
.catbtn:hover{border-color:var(--border-strong)}
.catpanel{position:absolute;left:0;right:0;top:calc(100% + 4px);z-index:10;border:1px solid var(--border-strong);
  border-radius:var(--radius-lg);background:var(--surface-1);box-shadow:0 8px 24px rgba(0,0,0,.4);
  padding:5px;max-height:200px;overflow-y:auto}
.catpanel input{width:100%;font:inherit;font-size:11.5px;color:var(--text);background:var(--surface-2);
  border:1px solid var(--border);border-radius:var(--radius);padding:4px 7px;outline:none;margin-bottom:4px}
.catitem{display:flex;align-items:center;gap:7px;width:100%;border:0;background:transparent;color:var(--text);
  font:inherit;font-size:11.5px;padding:5px 7px;border-radius:var(--radius);cursor:pointer;text-align:left}
.catitem:hover{background:var(--surface-3)}
.catitem.on{background:var(--nav-active);color:var(--accent)}
.catitem .n{margin-left:auto;color:var(--text-faint);font-variant-numeric:tabular-nums;font-size:10.5px}
.catitem .ck{width:12px;flex-shrink:0;color:var(--accent);font-size:11px}

.tagrow{display:flex;align-items:center;gap:5px;flex-wrap:wrap;font-size:11px}
.tglbl{color:var(--text-faint);font-weight:600}
.ftag{display:inline-flex;align-items:center;gap:4px;background:var(--surface-3);border:1px solid var(--border);
  border-radius:20px;padding:1px 8px;color:var(--text-dim);font-weight:500}
.ftag button{border:0;background:transparent;color:var(--text-faint);cursor:pointer;font-size:10px;padding:0}
.ftag button:hover{color:var(--danger,#e2483d)}
.tagadd{position:relative}
.addtag{border:1px dashed var(--border-strong);background:transparent;color:var(--text-faint);
  border-radius:20px;padding:1px 8px;font-size:11px;cursor:pointer}
.addtag:hover{color:var(--text);border-color:var(--text-faint)}
.tagadd input{width:96px;font:inherit;font-size:11px;color:var(--text);background:var(--surface-2);
  border:1px solid var(--border);border-radius:20px;padding:2px 8px;outline:none}
.tagpanel{position:absolute;left:0;top:calc(100% + 4px);z-index:10;min-width:140px;border:1px solid var(--border-strong);
  border-radius:var(--radius-lg);background:var(--surface-1);box-shadow:0 8px 24px rgba(0,0,0,.4);
  padding:4px;max-height:170px;overflow-y:auto}
.tagpanel button{display:flex;gap:8px;width:100%;border:0;background:transparent;color:var(--text);font:inherit;
  font-size:11.5px;padding:4px 7px;border-radius:var(--radius);cursor:pointer;text-align:left}
.tagpanel button:hover{background:var(--surface-3)}
.tagpanel .n{margin-left:auto;color:var(--text-faint);font-size:10.5px}

/* the list is the widget's heart: native scroll, any number of pins */
.pwlist{flex:1;min-height:0;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:6px}
.pwhint{padding:16px;font-size:12px;color:var(--text-faint);text-align:center}
.plink{border:0;background:transparent;color:var(--accent);cursor:pointer;font-size:12px;padding:2px 0}
.psep{margin:0 6px;opacity:.5}

.brow{position:relative;flex-shrink:0;border:1px solid var(--border);border-left:3px solid var(--cat,#738496);
  border-radius:8px;background:var(--surface-2);padding:6px 8px 7px}
.brow:hover{border-color:var(--border-strong);border-left-color:var(--cat,#738496)}
.brow.dropbefore{box-shadow:0 -2px 0 0 var(--accent)}
.brow.flash{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 40%,transparent)}
.brow .r1{display:flex;align-items:center;gap:6px;min-height:20px}
.grip{color:var(--text-faint);font-size:10px;cursor:grab;flex-shrink:0}
.bname{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bnamein{flex:1;min-width:0;font:inherit;font-size:12px;font-weight:600;color:var(--text);background:var(--surface-1);
  border:1px solid var(--accent);border-radius:4px;padding:1px 4px;outline:none}
.lbadge{flex-shrink:0;font-size:8.5px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;
  border:1px solid var(--border-strong);color:var(--text-faint);border-radius:10px;padding:0 5px}
.pol{margin-left:auto;flex-shrink:0;font-size:11px;font-weight:700;color:var(--text-faint)}
.pol.neg{color:var(--danger,#e2483d)}
.ricon{flex-shrink:0;border:1px solid var(--border-strong);background:var(--surface-1);color:var(--text-dim);
  border-radius:5px;height:20px;min-width:20px;font-size:11px;font-weight:600;line-height:1;cursor:pointer;padding:0 4px}
.ricon:hover{color:var(--accent);border-color:var(--accent)}
.ricon.x:hover{color:var(--danger,#e2483d);border-color:var(--danger,#e2483d)}
.ricon.act{opacity:0;transition:opacity .1s}
.brow:hover .ricon.act,.ricon.act.done{opacity:1}
.ricon.done{color:var(--ok,#3aa675);border-color:var(--ok,#3aa675)}
.pinbtn{padding:0 6px}
.updbadge{flex-shrink:0;border:1px solid color-mix(in srgb,var(--warn,#b65c02) 50%,var(--border));
  background:color-mix(in srgb,var(--warn,#b65c02) 12%,transparent);color:var(--warn,#b65c02);
  border-radius:10px;padding:0 6px;font:inherit;font-size:9.5px;font-weight:700;cursor:pointer;white-space:nowrap}
.updbadge:hover{background:color-mix(in srgb,var(--warn,#b65c02) 22%,transparent)}
.explbl{font-size:9.5px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:var(--text-faint);margin:6px 0 3px}
.exptags{display:flex;flex-wrap:wrap;gap:4px}
.exptags span{font-size:10.5px;background:var(--surface-3);border:1px solid var(--border);border-radius:20px;
  padding:1px 8px;color:var(--text-dim)}
.exphint{font-size:11px;color:var(--text-faint)}
.btext{font-size:11px;color:var(--text-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.btextview{font-size:11.5px;color:var(--text);background:var(--surface-1);border:1px solid var(--border);
  border-radius:5px;padding:6px;line-height:1.5;user-select:text;white-space:pre-wrap;word-break:break-word}
.btextarea{width:100%;min-height:64px;margin-top:5px;resize:vertical;font:inherit;font-size:11.5px;color:var(--text);
  background:var(--surface-1);border:1px solid var(--border);border-radius:5px;padding:6px;outline:none}
.btextarea:focus{border-color:var(--accent)}
.brow.pinned{opacity:.55;cursor:pointer}
.brow.pinned:hover{opacity:.8}
.pinchip{flex-shrink:0;font-size:10px;font-weight:700;color:var(--ok,#3aa675)}

.newrow{flex-shrink:0;border:1px solid var(--border);border-radius:8px;background:transparent;color:var(--text-dim);
  font:inherit;font-size:11.5px;font-weight:600;padding:7px;cursor:pointer}
.newrow:hover{color:var(--accent);border-color:var(--accent)}

.skel{flex-shrink:0;height:40px;border-radius:8px;background:linear-gradient(90deg,var(--surface-2),var(--surface-3),var(--surface-2));
  background-size:200% 100%;animation:sh 1.4s ease infinite}
@keyframes sh{to{background-position:-200% 0}}
@media (prefers-reduced-motion: reduce){.skel{animation:none;background:var(--surface-2)}}

.pwfoot{flex-shrink:0;display:flex;align-items:center;gap:8px;border-top:1px solid var(--border);
  background:var(--surface-1);padding:6px 12px;font-size:11px;color:var(--text-faint)}
.plib{margin-left:auto;color:var(--accent);cursor:pointer;font-weight:600;text-decoration:none}
</style>
