<script setup lang="ts">
import { computed, nextTick, onActivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { ApiError, deleteBlock, deleteCategory, defaultCategories, listBlocks, listCategories, listExamples, listTags, reorderCategories, resolveBlocks, restoreCategories, saveBlock, saveCategory, type ExampleImage } from '../api'
import { useToast } from '../composables/useToast'
import { useConfirm } from '../composables/useConfirm'
import LibraryImport from '../components/LibraryImport.vue'
import { newId } from '../vault/ids'
import { groupByCategory } from './librarySections'
import type { CategoryCount, LibraryBlock, TagCount } from '../types'

const props = defineProps<{
  // A work-local canvas block being saved to the vault: open the editor drawer prefilled.
  draftBlock?: { block: LibraryBlock; nonce: number } | null
  // Deep-link filter from the prompt widget's "Open in Library ↗": pre-select category + tags.
  filter?: { category: string; tags: string[]; nonce: number } | null
}>()
const emit = defineEmits<{ 'draft-saved': [LibraryBlock] }>()
const { push } = useToast()
const { confirm } = useConfirm()

const PALETTE = ['#6e5dc6', '#0c66e4', '#ae4787', '#1f845a', '#b65c02', '#12b5a6', '#d4537e', '#e2483d', '#2fb8c6', '#738496']

const categories = ref<CategoryCount[]>([])
const blocks = ref<LibraryBlock[]>([]) // accumulated across pages (infinite scroll)
const total = ref(0)
const page = ref(1)
const perPage = 48
const activeCategory = ref('') // '' = all
const selectedTags = ref<string[]>([])
const tagOptions = ref<TagCount[]>([])
const search = ref('')
const loading = ref(false)
const noVault = ref(false)

const colorBySlug = computed(() => Object.fromEntries(categories.value.map((c) => [c.slug, c.color])))
const catColor = (slug: string) => colorBySlug.value[slug] || '#738496'
const catName = (slug: string) => categories.value.find((c) => c.slug === slug)?.name || slug
const catCount = (slug: string) => categories.value.find((c) => c.slug === slug)?.count ?? 0
const allCount = computed(() => categories.value.reduce((n, c) => n + c.count, 0))
const hasMore = computed(() => blocks.value.length < total.value)
// One section per consecutive category run — sort=category makes categories contiguous across pages.
const sections = computed(() => groupByCategory(blocks.value))

async function loadCategories() {
  try {
    categories.value = await listCategories(selectedTags.value) // counts reflect the active tag filter
    noVault.value = false
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) noVault.value = true
    else push(e instanceof Error ? e.message : 'Could not load categories', 'err')
  }
}
async function loadTags() {
  try { tagOptions.value = await listTags(activeCategory.value) } catch { tagOptions.value = [] }
}
let blocksReq = 0
// reset=true replaces the list (category/tag/search change); reset=false appends the next page.
async function loadPage(reset: boolean) {
  if (!reset && (loading.value || !hasMore.value)) return
  const req = ++blocksReq // a slow earlier response must not overwrite a newer filter's result
  loading.value = true
  try {
    const p = reset ? 1 : page.value + 1
    const res = await listBlocks({
      categories: activeCategory.value ? [activeCategory.value] : [], tags: selectedTags.value,
      search: search.value, sort: 'category', page: p, perPage,
    })
    if (req !== blocksReq) return // superseded
    page.value = p
    blocks.value = reset ? res.items : [...blocks.value, ...res.items]
    total.value = res.total
    noVault.value = false
    // After a reset the fresh list may be shorter than the viewport — top up until the sentinel
    // is pushed out of view (the observer only fires on an intersection *change*).
    nextTick(fillViewport)
  } catch (e) {
    if (req !== blocksReq) return
    if (e instanceof ApiError && e.status === 409) { noVault.value = true; blocks.value = []; total.value = 0 }
    else push(e instanceof Error ? e.message : 'Could not load blocks', 'err')
  } finally {
    if (req === blocksReq) loading.value = false
  }
}
function reload() { clearSelection(); page.value = 1; loadPage(true) }
async function refreshAll() {
  await loadCategories()
  await Promise.all([reload(), loadTags()])
}
// onActivated also fires on first mount under KeepAlive, so a separate setup-time call would double-load.
onActivated(refreshAll)

// Deep-link from the prompt widget's footer: adopt its category + tags, then reload. `immediate` so
// the very first navigation applies too — under KeepAlive the prop is already set when the view first
// mounts, so a plain watcher would miss it (no change to observe).
watch(() => props.filter?.nonce, () => {
  if (!props.filter) return
  activeCategory.value = props.filter.category
  selectedTags.value = [...props.filter.tags]
  search.value = ''
  refreshAll()
}, { immediate: true })

// ---- infinite scroll: an IntersectionObserver on a bottom sentinel within the scroll area ----
const gridEl = ref<HTMLElement | null>(null)
const sentinel = ref<HTMLElement | null>(null)
let observer: IntersectionObserver | null = null
function fillViewport() {
  // Re-arm the observer against the sentinel's current position so a short list keeps loading until
  // the sentinel leaves the root+rootMargin band (or there's nothing more).
  const el = sentinel.value
  if (!observer || !el) return
  observer.unobserve(el)
  observer.observe(el)
}
watch([gridEl, sentinel], ([root, el]) => {
  observer?.disconnect()
  observer = null
  if (!root || !el) return
  observer = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting && hasMore.value && !loading.value) loadPage(false)
  }, { root, rootMargin: '800px' })
  observer.observe(el)
})

// Bulk import (modal). On success, reload so the imported blocks + any new categories show.
const importing = ref(false)
async function onImportDone() { importing.value = false; await refreshAll() }

// ---- export (client-side JSON, import-ready): selected · current filter · entire library ----
const exportOpen = ref(false)
const exportEl = ref<HTMLElement | null>(null)
const exporting = ref(false)
function toggleExport() { exportOpen.value = !exportOpen.value }
// Page through a filter to gather every match (large per-page to keep it to one or two round-trips).
async function fetchAllBlocks(opts: { categories?: string[]; tags?: string[]; search?: string }): Promise<LibraryBlock[]> {
  const out: LibraryBlock[] = []
  for (let p = 1; ; p++) {
    const res = await listBlocks({ ...opts, sort: 'category', page: p, perPage: 200 })
    out.push(...res.items)
    if (out.length >= res.total || !res.items.length) break
  }
  return out
}
// Keep only the fields the importer reads — drop the server-owned id/version/timestamps.
function toExport(items: LibraryBlock[]) {
  return items.map(({ category, name, text, polarity, tags }) => ({ category, name, text, polarity, tags }))
}
function downloadJson(data: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename
  document.body.appendChild(a); a.click(); a.remove()
  URL.revokeObjectURL(url)
}
async function exportScope(scope: 'selected' | 'filter' | 'all') {
  if (exporting.value) return
  exportOpen.value = false
  exporting.value = true
  try {
    const items = scope === 'selected' ? await resolveBlocks(selected.value)
      : scope === 'filter' ? await fetchAllBlocks({ categories: activeCategory.value ? [activeCategory.value] : [], tags: selectedTags.value, search: search.value })
        : await fetchAllBlocks({})
    if (!items.length) { push('Nothing to export', 'err'); return }
    downloadJson(toExport(items), `library-${scope}-${items.length}.json`)
    push(`Exported ${items.length} block${items.length === 1 ? '' : 's'}`, 'ok')
  } catch (e) {
    push(e instanceof Error ? e.message : 'Export failed', 'err')
  } finally { exporting.value = false }
}

// Built-in category set — the Manage modal diffs it against live categories for the built-in
// badge and the "restore deleted defaults" chips.
const defaults = ref<{ slug: string; name: string; color: string }[]>([])
const presentSlugs = computed(() => new Set(categories.value.map((c) => c.slug)))
const missingDefaults = computed(() => defaults.value.filter((d) => !presentSlugs.value.has(d.slug)))
const isBuiltin = (slug: string) => defaults.value.some((d) => d.slug === slug)

function selectCategory(slug: string) {
  if (activeCategory.value === slug) return
  activeCategory.value = slug
  reload()
  loadTags() // tag filter persists across category switches; only the tag *options* re-scope
}
function toggleTag(name: string) {
  const i = selectedTags.value.indexOf(name)
  if (i >= 0) selectedTags.value.splice(i, 1)
  else selectedTags.value.push(name)
  reload()
  loadCategories() // re-count categories under the new tag filter
}
let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(search, () => { if (searchTimer) clearTimeout(searchTimer); searchTimer = setTimeout(reload, 300) })

// ---- multi-select (bulk move / delete) ----
const selected = ref<string[]>([])
const selectedBlocks = computed(() => blocks.value.filter((b) => selected.value.includes(b.id)))
function toggleSelect(id: string) {
  const i = selected.value.indexOf(id)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(id)
}
function clearSelection() { selected.value = []; moveCatOpen.value = false }
const allSelected = computed(() => blocks.value.length > 0 && selected.value.length === blocks.value.length)
function toggleAll() {
  if (allSelected.value) clearSelection()
  else selected.value = blocks.value.map((b) => b.id) // selects every loaded block
}
async function deleteSelected() {
  const ids = [...selected.value]
  if (!ids.length) return
  if (!(await confirm({ title: 'Delete blocks', message: `Delete ${ids.length} selected block(s)? This can't be undone.`, confirmLabel: 'Delete', danger: true }))) return
  try {
    for (const id of ids) await deleteBlock(id)
    push(`Deleted ${ids.length} block(s)`, 'ok')
    clearSelection()
    await refreshAll()
  } catch (e) {
    push(e instanceof Error ? e.message : 'Delete failed', 'err')
  }
}

// ---- move selected blocks to another category (fixed popover off the selection bar button) ----
const moveCatOpen = ref(false)
const moveCatEl = ref<HTMLElement | null>(null)
const moveCatStyle = ref<Record<string, string>>({})
function openMoveCat(e: MouseEvent) {
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
  moveCatStyle.value = { left: `${r.left}px`, top: `${r.bottom + 5}px`, minWidth: `${r.width}px` }
  moveCatOpen.value = !moveCatOpen.value
}
async function moveSelectedTo(slug: string) {
  moveCatOpen.value = false
  const items = selectedBlocks.value.filter((b) => b.category !== slug)
  if (!items.length) { clearSelection(); return }
  try {
    for (const b of items) await saveBlock({ ...b, category: slug })
    push(`Moved ${items.length} block(s) to ${catName(slug)}`, 'ok')
    clearSelection()
    await refreshAll()
  } catch (e) { push(e instanceof Error ? e.message : 'Move failed', 'err') }
}

// ---- quick create-category modal (opened from the block editor's "New category…") ----
const catModal = ref<{ name: string; color: string; onCreated?: (slug: string) => void } | null>(null)
function openCreateCategory(onCreated?: (slug: string) => void) {
  catModal.value = { name: '', color: PALETTE[0], onCreated }
}
function closeCatModal() { catModal.value = null }
async function saveCatModal() {
  const m = catModal.value
  if (!m) return
  if (!m.name.trim()) { push('Category name is required', 'err'); return }
  try {
    const res = await saveCategory(m.name.trim(), m.color)
    await loadCategories()
    const cb = m.onCreated
    catModal.value = null
    if (cb) cb(res.slug)
  } catch (e) {
    push(e instanceof Error ? e.message : 'Save failed', 'err')
  }
}

// ---- category manager modal: rename / recolor / delete / add / restore, all in one place ----
const manageOpen = ref(false)
const newCatName = ref('')
const recolorSlug = ref<string | null>(null)
const recolorStyle = ref<Record<string, string>>({})
const recolorCat = computed(() => categories.value.find((c) => c.slug === recolorSlug.value) ?? null)
async function openManage() {
  if (!defaults.value.length) {
    try { defaults.value = await defaultCategories() } catch { /* restore chips + built-in badges just won't show */ }
  }
  manageOpen.value = true
}
function closeManage() { manageOpen.value = false; recolorSlug.value = null; newCatName.value = '' }
async function renameCategory(c: CategoryCount, name: string) {
  const n = name.trim()
  if (!n || n === c.name) return
  try { await saveCategory(n, c.color, c.slug); await loadCategories() } // same slug — rename only relabels
  catch (e) { push(e instanceof Error ? e.message : 'Rename failed', 'err') }
}
function toggleRecolor(e: MouseEvent, slug: string) {
  if (recolorSlug.value === slug) { recolorSlug.value = null; return }
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
  recolorStyle.value = { left: `${r.left}px`, top: `${r.bottom + 6}px` }
  recolorSlug.value = slug
}
async function recolor(color: string) {
  const c = recolorCat.value
  recolorSlug.value = null
  if (!c || color.toLowerCase() === c.color.toLowerCase()) return
  try { await saveCategory(c.name, color, c.slug); await loadCategories() }
  catch (e) { push(e instanceof Error ? e.message : 'Recolor failed', 'err') }
}
async function addCategory() {
  const n = newCatName.value.trim()
  if (!n) return
  const used = new Set(categories.value.map((c) => c.color.toLowerCase()))
  const color = PALETTE.find((p) => !used.has(p.toLowerCase())) ?? PALETTE[0] // first unused palette hue
  try { await saveCategory(n, color); newCatName.value = ''; await loadCategories() }
  catch (e) { push(e instanceof Error ? e.message : 'Add failed', 'err') }
}
async function restoreDefault(slug: string) {
  try { await restoreCategories([slug]); await refreshAll(); push('Category restored', 'ok') }
  catch (e) { push(e instanceof Error ? e.message : 'Restore failed', 'err') }
}

// ---- drag-to-reorder categories (persisted as one shared order) ----
const dragSlug = ref<string | null>(null)
const dragOverSlug = ref<string | null>(null)
function onCatDragStart(slug: string, e: DragEvent) {
  dragSlug.value = slug
  recolorSlug.value = null
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'
}
function onCatDragOver(slug: string) {
  if (dragSlug.value && slug !== dragSlug.value) dragOverSlug.value = slug
}
function onCatDragEnd() { dragSlug.value = null; dragOverSlug.value = null }
async function onCatDrop(targetSlug: string) {
  const from = dragSlug.value
  dragSlug.value = null; dragOverSlug.value = null
  if (!from || from === targetSlug) return
  const order = categories.value.map((c) => c.slug)
  const fi = order.indexOf(from)
  if (fi < 0 || !order.includes(targetSlug)) return
  const [moved] = order.splice(fi, 1)
  order.splice(order.indexOf(targetSlug), 0, moved) // drop before the target row
  const bySlug = new Map(categories.value.map((c) => [c.slug, c]))
  categories.value = order.map((s) => bySlug.get(s)!).filter(Boolean) // optimistic
  try { await reorderCategories(order) }
  catch (e) { push(e instanceof Error ? e.message : 'Reorder failed', 'err'); await loadCategories() }
}
async function removeCategory(c: CategoryCount) {
  const message = c.count > 0
    ? `Delete category “${c.name}”? Its ${c.count} block${c.count > 1 ? 's' : ''} will move to Custom.`
    : `Delete category “${c.name}”?`
  if (!(await confirm({ title: 'Delete category', message, confirmLabel: 'Delete', danger: true }))) return
  try {
    await deleteCategory(c.slug)
    if (activeCategory.value === c.slug) selectCategory('')
    push('Category deleted', 'ok')
    await refreshAll()
  } catch (e) {
    push(e instanceof Error ? e.message : 'Delete failed', 'err')
  }
}

// ---- block-editor category selector: click-to-open, close outside ----
const blockCatOpen = ref(false)
const blockCatEl = ref<HTMLElement | null>(null)
const blockCatStyle = ref<Record<string, string>>({})
function toggleBlockCat(e: MouseEvent) {
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
  blockCatStyle.value = { left: `${r.left}px`, top: `${r.bottom + 4}px`, width: `${r.width}px` }
  blockCatOpen.value = !blockCatOpen.value
}
function chooseBlockCat(slug: string) {
  if (editor.value) editor.value.block.category = slug
  blockCatOpen.value = false
}
function newCategoryForBlock() {
  blockCatOpen.value = false
  openCreateCategory((slug) => { if (editor.value) editor.value.block.category = slug })
}
function onDocPointer(e: MouseEvent) {
  const t = e.target as Node
  if (blockCatEl.value && !blockCatEl.value.contains(t)) blockCatOpen.value = false
  if (moveCatEl.value && !moveCatEl.value.contains(t)) moveCatOpen.value = false
  if (exportEl.value && !exportEl.value.contains(t)) exportOpen.value = false
}
onMounted(() => document.addEventListener('mousedown', onDocPointer))
onUnmounted(() => {
  document.removeEventListener('mousedown', onDocPointer)
  if (searchTimer) clearTimeout(searchTimer) // don't let a debounced load fire after teardown
  if (exTimer) clearTimeout(exTimer)
  observer?.disconnect()
})

// ---- block editor drawer (converted to a centered modal in a later increment) ----
const editor = ref<{ isNew: boolean; fromDraft?: boolean; block: LibraryBlock } | null>(null)
const edTagInput = ref('')
const allTags = ref<TagCount[]>([])
const tagFocus = ref(false)

// Example images for the block: generated images that share the most tags with it.
const examples = ref<ExampleImage[]>([])
const showExamples = ref(true) // header toggle; persists across opens
const lightbox = ref<string | null>(null)
let exTimer: ReturnType<typeof setTimeout> | null = null
async function loadExamples() {
  const tags = editor.value?.block.tags ?? []
  if (!tags.length) { examples.value = []; return }
  try { examples.value = await listExamples(tags, 12) } catch { examples.value = [] }
}
watch(() => editor.value?.block.tags, () => {
  if (exTimer) clearTimeout(exTimer)
  exTimer = setTimeout(loadExamples, 250)
}, { deep: true })

async function openNew() {
  editor.value = { isNew: true, block: { id: newId('block'), category: activeCategory.value || 'custom', name: '', text: '', polarity: 'positive', tags: [] } }
  edTagInput.value = ''; examples.value = []
  allTags.value = await listTags('')
}
async function openEdit(b: LibraryBlock) {
  editor.value = { isNew: false, block: { ...b, tags: [...b.tags] } }
  edTagInput.value = ''
  allTags.value = await listTags('')
  loadExamples()
}

// A canvas-local block arriving to be saved: prefill the drawer. `immediate` covers the mount
// that the App's tab switch just triggered (the nonce is already set when this view appears).
watch(() => props.draftBlock?.nonce, async () => {
  const d = props.draftBlock
  if (!d) return
  editor.value = { isNew: true, fromDraft: true, block: { ...d.block, tags: [...d.block.tags] } }
  edTagInput.value = ''
  allTags.value = await listTags('')
  loadExamples()
}, { immediate: true })
function closeEditor() { editor.value = null; blockCatOpen.value = false; examples.value = [] }

const edTagMatches = computed(() => {
  const q = edTagInput.value.trim().toLowerCase()
  const chosen = new Set(editor.value?.block.tags ?? [])
  return allTags.value.filter((t) => !chosen.has(t.name) && (!q || t.name.toLowerCase().includes(q)))
})
function addEdTag(name: string) {
  const n = name.trim()
  if (!n || !editor.value) return
  if (!editor.value.block.tags.includes(n)) editor.value.block.tags.push(n)
  edTagInput.value = ''
}
function removeEdTag(name: string) {
  if (editor.value) editor.value.block.tags = editor.value.block.tags.filter((t) => t !== name)
}

const canSaveBlock = computed(() =>
  !!editor.value && editor.value.block.name.trim().length > 0 && editor.value.block.text.trim().length > 0,
)
async function saveEditor() {
  if (!editor.value) return
  const b = editor.value.block // category is already a slug chosen via the selector — no rename side effects
  const fromDraft = !!editor.value.fromDraft
  if (!b.name.trim()) { push('Block name is required', 'err'); return }
  if (!b.text.trim()) { push('Prompt text is required', 'err'); return }
  try {
    await saveBlock(b)
    push(editor.value.isNew ? 'Block created' : 'Block saved', 'ok')
    closeEditor()
    await refreshAll()
    // A saved canvas draft links its palette pin back in Generate (version 1 — a fresh vault block).
    if (fromDraft) emit('draft-saved', { ...b, tags: [...b.tags], version: 1 })
  } catch (e) {
    push(e instanceof Error ? e.message : 'Save failed', 'err')
  }
}
async function removeBlock(b: LibraryBlock) {
  if (!(await confirm({ title: 'Delete block', message: `Delete “${b.name || 'block'}”? This can't be undone.`, confirmLabel: 'Delete', danger: true }))) return
  try { await deleteBlock(b.id); push('Block deleted', 'ok'); closeEditor(); await refreshAll() }
  catch (e) { push(e instanceof Error ? e.message : 'Delete failed', 'err') }
}
</script>

<template>
  <section class="library">
    <!-- clean title row — just the section name + count -->
    <div class="lhead">
      <h1>Library</h1><span class="count">· {{ allCount }} blocks</span>
    </div>

    <div v-if="noVault" class="empty">
      <div class="emoji">❏</div>
      <p>No vault folder chosen yet. Pick one in <b>Settings</b> to start building your prompt library.</p>
    </div>

    <div v-else class="lbody">
      <!-- left rail — categories + a single Manage entry point -->
      <div class="rail">
        <div class="railhd">
          <span>Categories</span>
          <button class="manage" title="Manage categories" @click="openManage"><span>⚙</span> Manage</button>
        </div>
        <div class="catlist">
          <div class="catrow" :class="{ on: activeCategory === '' }" @click="selectCategory('')">
            <span class="cdot" style="background:var(--text-dim)"></span><span class="cn">All blocks</span><span class="cc">{{ allCount }}</span>
          </div>
          <div v-for="c in categories" :key="c.slug" class="catrow" :class="{ on: activeCategory === c.slug }"
            :style="{ '--cat': c.color }" @click="selectCategory(c.slug)">
            <span class="cdot"></span>
            <span class="cn">{{ c.name }}</span>
            <button class="ce" title="Manage categories" @click.stop="openManage">✎</button>
            <span class="cc">{{ c.count }}</span>
          </div>
        </div>
      </div>

      <!-- content column: toolbar · tag pins · selection bar · sectioned grid -->
      <div class="content">
        <div class="toolbar">
          <div class="search">
            <span class="ic">⌕</span>
            <input v-model="search" placeholder="Search name or text…" />
          </div>
          <div class="tbactions">
            <button class="tbtn" @click="importing = true"><span>⭳</span> Import</button>
            <div class="expwrap" ref="exportEl">
              <button class="tbtn" :class="{ busy: exporting }" @click="toggleExport"><span>⭱</span> Export <span class="car">▾</span></button>
              <div v-if="exportOpen" class="expmenu">
                <button :disabled="!selected.length" @click="exportScope('selected')">
                  Export selected<small>{{ selected.length ? `${selected.length} block${selected.length === 1 ? '' : 's'} currently selected` : 'no blocks selected' }}</small>
                </button>
                <button @click="exportScope('filter')">
                  Export current filter<small>everything matching the active category + tags + search</small>
                </button>
                <button @click="exportScope('all')">
                  Export entire library<small>all {{ allCount }} blocks</small>
                </button>
              </div>
            </div>
            <button class="tbtn primary" @click="openNew"><span>＋</span> New block</button>
          </div>
        </div>

        <div class="tagbar">
          <span class="taglbl">Tags · {{ activeCategory ? catName(activeCategory) : 'all' }}</span>
          <div v-if="tagOptions.length" class="tagwrap">
            <button v-for="t in tagOptions" :key="t.name" class="tchip" :class="{ on: selectedTags.includes(t.name) }" @click="toggleTag(t.name)">
              {{ t.name }} <span class="n">{{ t.count }}</span>
            </button>
          </div>
          <span v-else class="tagempty">No tags {{ activeCategory ? `in ${catName(activeCategory)}` : 'yet' }}.</span>
        </div>

        <div v-if="selected.length" class="selbar" ref="moveCatEl">
          <button class="selall" @click="toggleAll">
            <span class="box on">✓</span> {{ allSelected ? 'Deselect all' : 'Select all' }}
          </button>
          <span class="scount">· {{ selected.length }} selected</span>
          <div class="selsp"></div>
          <button class="sbtn" @click="openMoveCat">↔ Move to category</button>
          <button class="sbtn del" @click="deleteSelected">🗑 Delete</button>
          <button class="sbtn" @click="clearSelection">Clear</button>
          <div v-if="moveCatOpen" class="movepop" :style="moveCatStyle" @click.stop>
            <div v-for="c in categories" :key="c.slug" class="mo" @click="moveSelectedTo(c.slug)">
              <span class="cdot" :style="{ background: c.color }"></span>{{ c.name }}
            </div>
          </div>
        </div>

        <div class="gridscroll" ref="gridEl">
          <div v-if="!loading && !blocks.length" class="gridempty">
            <p>No blocks here yet. Click <b>＋ New block</b> to author one.</p>
          </div>
          <template v-for="sec in sections" :key="sec.slug">
            <div class="cathead">
              <span class="cdot" :style="{ background: catColor(sec.slug) }"></span>
              <span class="cn">{{ catName(sec.slug) }}</span>
              <span class="cc">{{ catCount(sec.slug) }} blocks</span>
            </div>
            <div class="grid">
              <div v-for="b in sec.items" :key="b.id" class="bcard" :class="{ sel: selected.includes(b.id), neg: b.polarity === 'negative' }"
                :style="{ '--cat': b.polarity === 'negative' ? 'var(--danger)' : catColor(b.category) }" @click="toggleSelect(b.id)">
                <div class="top">
                  <span class="selbox">{{ selected.includes(b.id) ? '✓' : '' }}</span>
                  <span class="bn">{{ b.name || 'Untitled' }}</span>
                  <span class="polbadge" :class="b.polarity === 'negative' ? 'neg' : 'pos'">{{ b.polarity === 'negative' ? 'NEG' : 'POS' }}</span>
                </div>
                <div class="btext">{{ b.text || 'empty' }}</div>
                <div class="btags"><span v-for="t in b.tags" :key="t" class="btag">{{ t }}</span></div>
                <div class="acts">
                  <button class="act" @click.stop="openEdit(b)">✎ Edit</button>
                  <button class="act del" @click.stop="removeBlock(b)" title="Delete">🗑</button>
                </div>
              </div>
            </div>
          </template>
          <div ref="sentinel" class="sentinel"></div>
          <div v-if="loading" class="loadmore">↻ Loading…<span v-if="total"> {{ blocks.length }} of {{ total }}</span></div>
        </div>
      </div>

    </div>

    <!-- block editor — centered modal (form on the left, example images on the right) -->
    <Teleport to="body">
      <div v-if="editor" class="edit-back" @click="closeEditor">
        <div class="edit" :class="{ noex: !showExamples }" @click.stop>
          <div class="edit-hd">
            <span class="ttl">{{ editor.isNew ? 'New block' : 'Edit block' }}</span>
            <div class="extoggle">
              <button :class="{ on: showExamples }" @click="showExamples = true">Examples on</button>
              <button :class="{ on: !showExamples }" @click="showExamples = false">off</button>
            </div>
            <button class="x" title="Close" @click="closeEditor">✕</button>
          </div>
          <div class="edit-body">
            <div class="medit">
              <div class="fld"><label>Name</label><input v-model="editor.block.name" placeholder="Block name" /></div>
              <div class="row2">
                <div class="fld"><label>Category</label>
                  <div class="catselect" ref="blockCatEl">
                    <button class="catselbtn" @click="toggleBlockCat">
                      <span class="cdot" :style="{ background: catColor(editor.block.category) }"></span>
                      <span class="cn">{{ catName(editor.block.category) }}</span>
                      <span class="chev">▾</span>
                    </button>
                    <div v-if="blockCatOpen" class="catseldrop" :style="blockCatStyle">
                      <div v-for="c in categories" :key="c.slug" class="co" @click="chooseBlockCat(c.slug)">
                        <span class="cdot" :style="{ background: c.color }"></span>{{ c.name }}
                      </div>
                      <div class="co create" @click="newCategoryForBlock">＋ New category…</div>
                    </div>
                  </div>
                </div>
                <div class="fld"><label>Polarity</label>
                  <div class="seg">
                    <button :class="{ on: editor.block.polarity === 'positive' }" @click="editor.block.polarity = 'positive'">＋ Positive</button>
                    <button class="neg" :class="{ on: editor.block.polarity === 'negative' }" @click="editor.block.polarity = 'negative'">− Negative</button>
                  </div>
                </div>
              </div>

              <div class="fld"><label>Text (prompt tags) <span class="req">· required</span></label><textarea v-model="editor.block.text" placeholder="1girl, silver hair, …"></textarea></div>

              <div class="fld"><label>Tags</label>
                <div class="tagedit">
                  <span v-for="t in editor.block.tags" :key="t" class="et">{{ t }} <b @click="removeEdTag(t)">✕</b></span>
                  <input class="ti" v-model="edTagInput" placeholder="Search or add…"
                    @focus="tagFocus = true" @blur="tagFocus = false" @keyup.enter="addEdTag(edTagInput)" />
                </div>
                <div v-if="tagFocus && (edTagMatches.length || edTagInput.trim())" class="accd">
                  <div v-for="t in edTagMatches" :key="t.name" class="tsopt" @mousedown.prevent="addEdTag(t.name)">
                    <span class="cn">{{ t.name }}</span><span class="cc">{{ t.count }}</span>
                  </div>
                  <div v-if="edTagInput.trim() && !allTags.some((t) => t.name === edTagInput.trim())" class="tsopt create" @mousedown.prevent="addEdTag(edTagInput)">
                    <span class="cn">＋ Create “{{ edTagInput.trim() }}”</span>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="showExamples" class="mexamples">
              <div class="exlbl">Examples — images sharing these tags</div>
              <div v-if="examples.length" class="examples">
                <img v-for="ex in examples" :key="ex.image_id" :src="`${ex.url}?w=400`" alt="example" loading="lazy"
                  title="Click to enlarge" @click="lightbox = ex.url" />
              </div>
              <div v-else class="prev"><b>Inherited by images</b>Images generated with this block carry its tags automatically — the most-matching ones show up here.</div>
            </div>
          </div>
          <div class="edit-ft">
            <button v-if="!editor.isNew" class="del" @click="removeBlock(editor.block)">🗑 Delete</button>
            <span class="sp"></span>
            <button class="cancel" @click="closeEditor">Cancel</button>
            <button class="save" :disabled="!canSaveBlock" @click="saveEditor">Save block</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- category manager modal: rename · recolor · delete · add · restore defaults -->
    <Teleport to="body">
      <div v-if="manageOpen" class="mng-back" @click="closeManage">
        <div class="mng" @click.stop="recolorSlug = null">
          <div class="mng-hd">Manage categories<button class="x" title="Done" @click="closeManage">✕</button></div>
          <div class="mng-add">
            <input v-model="newCatName" placeholder="New category name…" @keyup.enter="addCategory" />
            <button :disabled="!newCatName.trim()" @click="addCategory">＋ Add</button>
          </div>
          <div class="mng-list" @scroll="recolorSlug = null">
            <div class="cmlist">
              <div v-for="c in categories" :key="c.slug" class="cmrow"
                :class="{ dragging: dragSlug === c.slug, dragover: dragOverSlug === c.slug }"
                @dragover.prevent="onCatDragOver(c.slug)" @drop.prevent="onCatDrop(c.slug)" @dragleave="dragOverSlug = null">
                <span class="grip" title="Drag to reorder" draggable="true"
                  @dragstart="onCatDragStart(c.slug, $event)" @dragend="onCatDragEnd">⠿</span>
                <button class="cdot" :style="{ background: c.color }" title="Recolor" @click.stop="toggleRecolor($event, c.slug)"></button>
                <input class="nm" :value="c.name" @change="renameCategory(c, ($event.target as HTMLInputElement).value)"
                  @keyup.enter="($event.target as HTMLInputElement).blur()" />
                <span class="cnt">{{ c.count }}</span>
                <span v-if="isBuiltin(c.slug)" class="builtin">built-in</span>
                <button class="rm" title="Delete category" @click="removeCategory(c)">🗑</button>
              </div>
            </div>
          </div>
          <div v-if="missingDefaults.length" class="mng-restore">
            <div class="rl">Restore deleted defaults</div>
            <div class="rchips">
              <button v-for="d in missingDefaults" :key="d.slug" class="rchip" @click="restoreDefault(d.slug)">
                <span class="cdot" :style="{ background: d.color }"></span>＋ {{ d.name }}
              </button>
            </div>
          </div>
          <div class="mng-ft"><button class="done" @click="closeManage">Done</button></div>
        </div>
      </div>
      <!-- recolor swatch popover (fixed, so the scrolling modal body can't clip it) -->
      <div v-if="recolorSlug" class="swpop" :style="recolorStyle" @click.stop>
        <span v-for="col in PALETTE" :key="col" class="sw" :class="{ on: recolorCat && recolorCat.color.toLowerCase() === col }"
          :style="{ background: col }" @click="recolor(col)"></span>
        <label class="swhex" title="Custom color…">
          <input type="color" :value="recolorCat?.color || '#738496'" @change="recolor(($event.target as HTMLInputElement).value)" />
        </label>
      </div>
    </Teleport>

    <!-- quick create-category modal (from the block editor's "New category…") -->
    <Teleport to="body">
      <div v-if="catModal" class="catmodal-back" @click="closeCatModal">
        <div class="catmodal" @click.stop>
          <div class="cmhd">New category</div>
          <div class="fld"><label>Name</label>
            <input class="nameinput" v-model="catModal.name" placeholder="Category name" @keyup.enter="saveCatModal" />
          </div>
          <div class="fld"><label>Color</label>
            <div class="cmswatches">
              <span v-for="col in PALETTE" :key="col" class="sw" :class="{ on: catModal.color.toLowerCase() === col }"
                :style="{ background: col }" @click="catModal.color = col"></span>
              <label class="hexpick">
                <input type="color" v-model="catModal.color" />
                <span class="hexval">{{ catModal.color.toUpperCase() }}</span>
              </label>
            </div>
          </div>
          <div class="cmfoot">
            <span class="sp"></span>
            <button class="cancel" @click="closeCatModal">Cancel</button>
            <button class="save" @click="saveCatModal">Create</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- example image lightbox -->
    <Teleport to="body">
      <div v-if="lightbox" class="lightbox" @click="lightbox = null">
        <img :src="lightbox" alt="example" />
      </div>
    </Teleport>

    <LibraryImport v-if="importing" :categories="categories" @close="importing = false" @done="onImportDone" />
  </section>
</template>

<style scoped>
.library{flex:1;display:flex;flex-direction:column;min-width:0;background:var(--bg)}

/* clean title row */
.lhead{display:flex;align-items:center;gap:8px;padding:14px 18px;border-bottom:1px solid var(--border);flex-shrink:0}
.lhead h1{font-size:18px;font-weight:700;margin:0}
.lhead .count{font-size:13px;color:var(--text-faint)}

.empty{max-width:420px;margin:12vh auto;text-align:center;color:var(--text-dim)}
.empty .emoji{font-size:34px;color:var(--text-faint);margin-bottom:12px}
.empty p{font-size:13px;line-height:1.6}

.lbody{flex:1;display:flex;min-height:0}

/* left rail — categories */
.rail{width:210px;flex-shrink:0;border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden;background:color-mix(in srgb,var(--surface-1) 45%,transparent)}
.railhd{display:flex;align-items:center;font-size:10px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:var(--text-faint);padding:12px 12px 6px}
.railhd > span:first-child{margin-right:auto}
.railhd .manage{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface-2);color:var(--text-dim);font:inherit;font-size:11px;font-weight:600;padding:3px 8px;cursor:pointer}
.railhd .manage:hover{color:var(--accent);border-color:var(--accent)}
.catlist{flex:1;min-height:0;overflow-y:auto;padding:4px 8px 10px}
.catrow{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:var(--radius);color:var(--text-dim);font-size:12.5px;font-weight:600;margin-bottom:1px;cursor:pointer}
.catrow .cdot{width:9px;height:9px;border-radius:50%;background:var(--cat,#738496);flex-shrink:0}
.catrow .cn{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.catrow .cc{font-size:11px;color:var(--text-faint);font-weight:500;font-variant-numeric:tabular-nums}
.catrow:hover{background:var(--surface-3);color:var(--text)}
.catrow.on{background:var(--nav-active);color:var(--accent)}
.catrow.on .cc{color:var(--accent)}
.catrow .ce{margin-left:auto;width:18px;height:18px;flex-shrink:0;border:0;background:transparent;color:var(--text-faint);font-size:11px;line-height:1;cursor:pointer;border-radius:3px;opacity:0;padding:0}
.catrow:hover .ce{opacity:1}
.catrow .ce:hover{color:var(--accent);background:var(--surface-3)}
.catrow .ce + .cc{margin-left:6px}

/* content column */
.content{flex:1;min-width:0;display:flex;flex-direction:column}

/* toolbar row */
.toolbar{display:flex;align-items:center;gap:8px;padding:10px 16px;border-bottom:1px solid var(--border);flex-shrink:0}
.search{position:relative;flex:0 1 520px;min-width:200px}
.search .ic{position:absolute;left:10px;top:50%;transform:translateY(-50%);color:var(--text-faint);font-size:12px}
.search input{width:100%;font:inherit;font-size:13px;color:var(--text);background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px 8px 30px;outline:none}
.search input:focus{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 30%,transparent)}
.tbactions{margin-left:auto;display:flex;align-items:center;gap:8px}
.tbtn{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);padding:8px 12px;font:inherit;font-size:12.5px;font-weight:600;cursor:pointer;white-space:nowrap}
.tbtn:hover{color:var(--text)}
.tbtn.primary{background:var(--accent);color:var(--on-accent);border-color:transparent}
.tbtn.primary:hover{background:var(--accent-strong,#0055cc)}
.tbtn.busy{opacity:.6;cursor:progress}
.tbtn .car{font-size:9px;opacity:.8}
.expwrap{position:relative}
.expmenu{position:absolute;right:0;top:calc(100% + 4px);z-index:20;min-width:230px;border:1px solid var(--border-strong);border-radius:var(--radius-lg);background:var(--surface-1);box-shadow:0 8px 24px rgba(0,0,0,.4);padding:5px}
.expmenu button{display:flex;flex-direction:column;gap:1px;width:100%;border:0;background:transparent;color:var(--text);font:inherit;font-size:12.5px;font-weight:600;padding:7px 9px;border-radius:var(--radius);cursor:pointer;text-align:left}
.expmenu button small{font-weight:500;color:var(--text-faint);font-size:11px}
.expmenu button:hover{background:var(--surface-3)}
.expmenu button:disabled{opacity:.45;cursor:default}
.expmenu button:disabled:hover{background:transparent}

/* tag pins */
.tagbar{display:flex;flex-direction:column;gap:6px;padding:9px 8px 9px 16px;border-bottom:1px solid var(--border);flex-shrink:0}
.taglbl{font-size:9.5px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint)}
.tagempty{font-size:11.5px;color:var(--text-faint)}
.tagwrap{display:flex;flex-wrap:wrap;gap:6px;max-height:60px;overflow-y:auto}
.tchip{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:20px;padding:2px 10px;font:inherit;font-size:11.5px;font-weight:600;cursor:pointer;white-space:nowrap}
.tchip .n{color:var(--text-faint);font-weight:500;font-size:10px}
.tchip:hover{border-color:var(--border-strong);color:var(--text)}
.tchip.on{background:var(--nav-active);border-color:color-mix(in srgb,var(--accent) 45%,var(--border));color:var(--accent)}
.tchip.on .n{color:var(--accent)}

/* selection bar */
.selbar{position:relative;display:flex;align-items:center;gap:10px;padding:8px 16px;border-bottom:1px solid var(--border);flex-shrink:0;background:color-mix(in srgb,var(--accent) 5%,transparent)}
.selall{display:inline-flex;align-items:center;gap:7px;border:0;background:transparent;color:var(--text-dim);font:inherit;font-size:12.5px;font-weight:600;cursor:pointer;padding:0}
.selall:hover{color:var(--text)}
.selall .box{width:16px;height:16px;border:1.5px solid var(--border-strong);border-radius:4px;display:inline-flex;align-items:center;justify-content:center;font-size:11px;color:var(--accent)}
.selall .box.on{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.selbar .scount{font-size:12px;color:var(--text-faint)}
.selbar .selsp{flex:1}
.selbar .sbtn{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);padding:6px 11px;font:inherit;font-size:12px;font-weight:600;cursor:pointer}
.selbar .sbtn:hover{color:var(--text)}
.selbar .sbtn.del:hover{color:var(--danger);border-color:var(--danger)}
.movepop{position:fixed;z-index:40;background:var(--surface-2);border:1px solid var(--border-strong);border-radius:var(--radius);overflow:hidden;max-height:280px;overflow-y:auto;box-shadow:0 8px 24px rgba(0,0,0,.45)}
.movepop .mo{display:flex;align-items:center;gap:8px;padding:8px 11px;font-size:12.5px;color:var(--text-dim);cursor:pointer}
.movepop .mo:hover{background:var(--surface-3);color:var(--text)}
.movepop .mo .cdot{width:9px;height:9px;border-radius:50%;flex-shrink:0}

/* sectioned grid */
.gridscroll{flex:1;min-height:0;overflow-y:auto;padding:14px 8px 14px 16px;margin-right:8px}
.gridempty{margin:10vh auto;color:var(--text-dim);font-size:13px;text-align:center}
.cathead{display:flex;align-items:center;gap:9px;margin:2px 0 10px;padding-top:6px}
.cathead:not(:first-child){margin-top:22px;border-top:1px solid var(--border);padding-top:16px}
.cathead .cdot{width:11px;height:11px;border-radius:50%;flex-shrink:0}
.cathead .cn{font-size:13px;font-weight:700}
.cathead .cc{font-size:11px;color:var(--text-faint);font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;align-content:start}
.bcard{border:1px solid var(--border);border-left:3px solid var(--cat,#738496);border-radius:var(--radius-lg);background:var(--surface-1);padding:10px 12px;cursor:pointer;display:flex;flex-direction:column;transition:border-color .1s,box-shadow .1s}
.bcard:hover{border-color:var(--border-strong)}
.bcard.sel{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.bcard .top{display:flex;align-items:center;gap:7px;margin-bottom:3px}
.bcard .selbox{width:15px;height:15px;border:1.5px solid var(--border-strong);border-radius:4px;flex-shrink:0;display:inline-flex;align-items:center;justify-content:center;font-size:10px;color:var(--accent);line-height:1}
.bcard.sel .selbox{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.bcard .bn{font-size:13px;font-weight:600;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bcard .polbadge{margin-left:auto;font-size:8.5px;font-weight:800;border-radius:9px;padding:0 6px;line-height:15px}
.bcard .polbadge.pos{color:var(--ok);border:1px solid color-mix(in srgb,var(--ok) 45%,var(--border));background:color-mix(in srgb,var(--ok) 12%,transparent)}
.bcard .polbadge.neg{color:var(--danger);border:1px solid color-mix(in srgb,var(--danger) 50%,var(--border));background:color-mix(in srgb,var(--danger) 12%,transparent)}
.bcard .btext{font-size:11.5px;color:var(--text-faint);line-height:1.45;margin:2px 0 7px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:33px}
.bcard .btags{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:9px;min-height:0}
.bcard .btag{font-size:10px;background:var(--surface-3);border:1px solid var(--border);border-radius:20px;padding:1px 7px;color:var(--text-dim)}
.bcard .acts{display:flex;gap:6px;margin-top:auto}
.bcard .act{flex:1;display:inline-flex;align-items:center;justify-content:center;gap:5px;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);padding:5px;font:inherit;font-size:11.5px;font-weight:600;cursor:pointer}
.bcard .act:hover{color:var(--accent);border-color:var(--accent)}
.bcard .act.del{flex:0 0 34px}
.bcard .act.del:hover{color:var(--danger);border-color:var(--danger)}

.sentinel{height:1px}
.loadmore{display:flex;align-items:center;justify-content:center;gap:8px;padding:22px;font-size:12px;color:var(--text-faint)}
.loadmore span{font-variant-numeric:tabular-nums;opacity:.7}

/* block editor — centered modal (teleported to body) */
.edit-back{position:fixed;inset:0;z-index:1600;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:24px}
.edit{width:min(760px,96vw);max-height:88vh;display:flex;flex-direction:column;border:1px solid var(--border-strong);border-radius:12px;background:var(--surface-1);box-shadow:0 20px 60px rgba(0,0,0,.5);overflow:hidden}
.edit.noex{width:min(520px,96vw)}
.edit-hd{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid var(--border);font-size:15px;font-weight:700}
.edit-hd .ttl{margin-right:auto}
.edit-hd .extoggle{display:inline-flex;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;font-size:11.5px}
.edit-hd .extoggle button{border:0;border-left:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);font:inherit;font-weight:600;padding:4px 10px;cursor:pointer}
.edit-hd .extoggle button:first-child{border-left:0}
.edit-hd .extoggle button.on{background:var(--nav-active);color:var(--accent)}
.edit-hd .x{border:0;background:transparent;color:var(--text-faint);font-size:16px;cursor:pointer}
.edit-hd .x:hover{color:var(--text)}
.edit-body{flex:1;min-height:0;overflow-y:auto;display:flex}
.medit{flex:1;min-width:0;padding:16px 18px;display:flex;flex-direction:column;gap:13px}
.row2{display:grid;grid-template-columns:1fr 160px;gap:12px}
.mexamples{width:280px;flex-shrink:0;border-left:1px solid var(--border);padding:16px;overflow-y:auto;background:color-mix(in srgb,var(--surface-2) 40%,transparent)}
.mexamples{display:flex;flex-direction:column}
.mexamples .exlbl{font-size:10.5px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;color:var(--text-faint);margin-bottom:10px;flex-shrink:0}
/* show ~6 examples (3 rows × 2); the rest scroll so the modal never stretches tall */
.mexamples .examples{min-height:0;max-height:500px;overflow-y:auto;padding-right:4px}
.fld{display:flex;flex-direction:column;gap:6px}
.fld label{font-size:11px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;color:var(--text-faint)}
.fld>input,.fld textarea{width:100%;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px;color:var(--text);font:inherit;font-size:13px;outline:none}
.fld textarea{min-height:92px;resize:vertical;line-height:1.5}
.fld>input:focus,.fld textarea:focus{border-color:var(--accent)}
.seg{display:flex;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.seg button{flex:1;border:0;background:transparent;color:var(--text-dim);font:inherit;font-size:12px;font-weight:600;padding:8px;cursor:pointer}
.seg button.on{background:var(--accent);color:#fff}
.seg button.neg.on{background:#e2483d}

/* block-editor category selector */
.catselect{position:relative}
.catselbtn{width:100%;display:flex;align-items:center;gap:8px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px;color:var(--text);font:inherit;font-size:13px;cursor:pointer}
.catselbtn:hover{border-color:var(--border-strong)}
.catselbtn .cdot{width:9px;height:9px;border-radius:2px;flex-shrink:0}
.catselbtn .cn{flex:1;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.catselbtn .chev{color:var(--text-faint);font-size:11px}
.catseldrop{position:fixed;z-index:30;background:var(--surface-2);border:1px solid var(--border-strong);border-radius:var(--radius);overflow:hidden;max-height:280px;overflow-y:auto;box-shadow:0 8px 24px rgba(0,0,0,.45)}
.catseldrop .co{display:flex;align-items:center;gap:8px;padding:8px 10px;font-size:12.5px;color:var(--text-dim);cursor:pointer}
.catseldrop .co:hover{background:var(--surface-3)}
.catseldrop .co .cdot{width:9px;height:9px;border-radius:2px;flex-shrink:0}
.catseldrop .co.create{color:var(--accent);font-weight:600;border-top:1px dashed var(--border)}

.hexpick{display:inline-flex;align-items:center;gap:6px;margin-left:2px;padding-left:8px;border-left:1px solid var(--border-strong);cursor:pointer}
.hexpick input[type=color]{width:18px;height:18px;padding:0;border:1px solid var(--border-strong);border-radius:4px;background:none;cursor:pointer}
.hexval{font-size:11px;font-weight:600;color:var(--text-dim);font-family:ui-monospace,monospace}

.tagedit{display:flex;flex-wrap:wrap;gap:6px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:7px}
.tagedit .ti{flex:1;min-width:80px;border:0;background:transparent;color:var(--text);font:inherit;font-size:12px;outline:none}
.et{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:var(--accent);background:var(--nav-active);border:1px solid color-mix(in srgb,var(--accent) 30%,transparent);border-radius:20px;padding:2px 8px}
.et b{color:var(--text-faint);font-weight:400;cursor:pointer}
.accd{margin-top:5px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;max-height:180px;overflow-y:auto}
.tsopt{display:flex;align-items:center;gap:8px;padding:6px 9px;font-size:12px;color:var(--text-dim);cursor:pointer}
.tsopt:hover{background:var(--surface-3)}
.tsopt .cn{flex:1}
.tsopt .cc{font-size:11px;color:var(--text-faint)}
.tsopt.create{color:var(--accent);font-weight:600;border-top:1px dashed var(--border)}
.examples{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.examples img{width:100%;aspect-ratio:3/4;object-fit:cover;border-radius:7px;border:1px solid var(--border);background:var(--surface-2);cursor:zoom-in;transition:border-color .1s}
.examples img:hover{border-color:var(--accent)}
.prev{background:var(--surface-2);border:1px dashed var(--border-strong);border-radius:var(--radius);padding:9px 11px;font-size:11.5px;color:var(--text-dim);line-height:1.5}
.prev b{color:var(--text-faint);font-weight:700;letter-spacing:.3px;text-transform:uppercase;font-size:10px;display:block;margin-bottom:3px}
.edit-ft{display:flex;align-items:center;gap:8px;padding:12px 18px;border-top:1px solid var(--border)}
.edit-ft .del{color:var(--warn);border:1px solid color-mix(in srgb,var(--warn) 40%,var(--border));background:transparent;border-radius:var(--radius);padding:7px 12px;font-size:12.5px;font-weight:600;cursor:pointer}
.edit-ft .del:hover{border-color:var(--danger);color:var(--danger)}
.edit-ft .sp{flex:1}
.edit-ft .cancel{border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text);border-radius:var(--radius);padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer}
.edit-ft .save{border:0;background:var(--accent);color:var(--on-accent);border-radius:var(--radius);padding:8px 16px;font-size:13px;font-weight:700;cursor:pointer}
.edit-ft .save:disabled{opacity:.5;cursor:default}
.fld label .req{color:var(--text-faint);font-weight:500}
</style>

<style>
/* teleported-to-body elements need global (unscoped) styles */
.lightbox{position:fixed;inset:0;z-index:2500;background:rgba(0,0,0,.82);display:flex;align-items:center;justify-content:center;padding:24px;cursor:zoom-out}
.lightbox img{max-width:min(92vw,900px);max-height:92vh;object-fit:contain;border-radius:8px;box-shadow:0 12px 48px rgba(0,0,0,.6)}
/* category modal is teleported to <body>, so its styles are global (not scoped to the view) */
.catmodal-back{position:fixed;inset:0;z-index:1500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:20px}
.catmodal{width:min(360px,100%);background:var(--surface-1);border:1px solid var(--border);border-radius:var(--radius-lg);box-shadow:0 18px 48px rgba(0,0,0,.5);padding:18px 20px;display:flex;flex-direction:column;gap:14px}
.catmodal .cmhd{font-size:15px;font-weight:650;color:var(--text)}
.catmodal .fld{display:flex;flex-direction:column;gap:6px}
.catmodal .fld label{font-size:11px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;color:var(--text-faint)}
.catmodal .nameinput{width:100%;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px;color:var(--text);font:inherit;font-size:13px;outline:none}
.catmodal .nameinput:focus{border-color:var(--accent)}
.catmodal .cmswatches{display:flex;flex-wrap:wrap;gap:7px;align-items:center}
.catmodal .cmswatches .sw{width:20px;height:20px;border-radius:50%;border:2px solid transparent;cursor:pointer}
.catmodal .cmswatches .sw.on{border-color:var(--text)}
.catmodal .hexpick{display:inline-flex;align-items:center;gap:6px;margin-left:2px;padding-left:8px;border-left:1px solid var(--border-strong);cursor:pointer}
.catmodal .hexpick input[type=color]{width:20px;height:20px;padding:0;border:1px solid var(--border-strong);border-radius:4px;background:none;cursor:pointer}
.catmodal .hexval{font-size:11px;font-weight:600;color:var(--text-dim);font-family:ui-monospace,monospace}
.catmodal .cmfoot{display:flex;align-items:center;gap:8px;margin-top:4px}
.catmodal .cmfoot .del{color:#e8913a;border:1px solid color-mix(in srgb,#b65c02 40%,var(--border));background:transparent;border-radius:var(--radius);padding:7px 12px;font-size:12px;font-weight:600;cursor:pointer}
.catmodal .cmfoot .sp{flex:1}
.catmodal .cmfoot .cancel{border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer}
.catmodal .cmfoot .save{border:0;background:var(--accent);color:#fff;border-radius:var(--radius);padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer}

/* category manager modal (teleported → global) */
.mng-back{position:fixed;inset:0;z-index:1500;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:24px}
.mng{width:min(560px,96vw);max-height:88vh;display:flex;flex-direction:column;border:1px solid var(--border-strong);border-radius:12px;background:var(--surface-1);box-shadow:0 20px 60px rgba(0,0,0,.5);overflow:hidden}
.mng-hd{display:flex;align-items:center;padding:14px 18px;border-bottom:1px solid var(--border);font-size:15px;font-weight:700;color:var(--text);flex-shrink:0}
.mng-hd .x{margin-left:auto;border:0;background:transparent;color:var(--text-faint);font-size:16px;cursor:pointer}
.mng-hd .x:hover{color:var(--text)}
/* add row pinned at the top; only the list scrolls, capped so the modal stays compact */
.mng-add{display:flex;gap:8px;padding:12px 18px;border-bottom:1px solid var(--border);flex-shrink:0}
.mng-add input{flex:1;font:inherit;font-size:13px;color:var(--text);background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px;outline:none}
.mng-add input:focus{border-color:var(--accent)}
.mng-add button{border:1px solid var(--accent);background:transparent;color:var(--accent);border-radius:var(--radius);padding:8px 14px;font:inherit;font-weight:700;cursor:pointer;white-space:nowrap}
.mng-add button:disabled{opacity:.45;cursor:default;border-color:var(--border-strong);color:var(--text-faint)}
.mng-list{flex:1;min-height:0;max-height:46vh;overflow-y:auto;padding:12px 18px}
.mng .cmlist{display:flex;flex-direction:column;gap:4px}
.mng .cmrow{display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface-2)}
.mng .cmrow.dragging{opacity:.45}
.mng .cmrow.dragover{border-color:var(--accent);box-shadow:0 -2px 0 var(--accent)}
.mng .cmrow .grip{flex-shrink:0;color:var(--text-faint);font-size:13px;line-height:1;cursor:grab;user-select:none;padding:0 1px}
.mng .cmrow .grip:hover{color:var(--text-dim)}
.mng .cmrow .grip:active{cursor:grabbing}
.mng .cmrow .cdot{width:14px;height:14px;border-radius:50%;flex-shrink:0;cursor:pointer;border:2px solid transparent;padding:0}
.mng .cmrow .cdot:hover{border-color:var(--text-faint)}
.mng .cmrow .nm{flex:1;min-width:0;font:inherit;font-size:13px;font-weight:600;color:var(--text);background:transparent;border:1px solid transparent;border-radius:4px;padding:3px 6px;outline:none}
.mng .cmrow .nm:hover{border-color:var(--border)}
.mng .cmrow .nm:focus{border-color:var(--accent);background:var(--surface-1)}
.mng .cmrow .cnt{font-size:11px;color:var(--text-faint);font-variant-numeric:tabular-nums}
.mng .cmrow .builtin{font-size:9px;font-weight:700;text-transform:uppercase;color:var(--text-faint);border:1px solid var(--border);border-radius:9px;padding:0 6px}
.mng .cmrow .rm{border:0;background:transparent;color:var(--text-faint);cursor:pointer;font-size:13px}
.mng .cmrow .rm:hover{color:var(--danger)}
.mng-restore{padding:12px 18px;border-top:1px solid var(--border);flex-shrink:0}
.mng-restore .rl{font-size:10.5px;font-weight:700;text-transform:uppercase;color:var(--text-faint);margin-bottom:8px}
.mng-restore .rchips{display:flex;flex-wrap:wrap;gap:6px}
.mng-restore .rchip{display:inline-flex;align-items:center;gap:6px;border:1px dashed var(--border-strong);background:transparent;color:var(--text-dim);border-radius:20px;padding:3px 10px;font:inherit;font-size:11.5px;font-weight:600;cursor:pointer}
.mng-restore .rchip:hover{border-color:var(--accent);color:var(--accent)}
.mng-restore .rchip .cdot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.mng-ft{display:flex;padding:12px 18px;border-top:1px solid var(--border);flex-shrink:0}
.mng-ft .done{margin-left:auto;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text);border-radius:var(--radius);padding:7px 16px;font:inherit;font-size:12.5px;font-weight:700;cursor:pointer}
.mng-ft .done:hover{border-color:var(--accent);color:var(--accent)}
.swpop{position:fixed;z-index:1600;display:flex;flex-wrap:wrap;align-items:center;gap:7px;width:168px;padding:9px;background:var(--surface-1);border:1px solid var(--border-strong);border-radius:var(--radius-lg);box-shadow:0 10px 30px rgba(0,0,0,.5)}
.swpop .sw{width:20px;height:20px;border-radius:50%;border:2px solid transparent;cursor:pointer}
.swpop .sw.on{border-color:var(--text)}
/* custom color: a rainbow-ringed swatch wrapping a hidden native color input */
.swpop .swhex{position:relative;width:20px;height:20px;border-radius:50%;overflow:hidden;cursor:pointer;border:2px solid var(--border-strong);background:conic-gradient(from 90deg,#f00,#ff0,#0f0,#0ff,#00f,#f0f,#f00)}
.swpop .swhex input{position:absolute;inset:-6px;width:200%;height:200%;padding:0;border:0;background:none;cursor:pointer;opacity:0}
</style>
