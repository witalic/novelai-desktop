<script setup lang="ts">
import { computed, nextTick, onActivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { ApiError, deleteBlock, deleteCategory, defaultCategories, listBlocks, listCategories, listExamples, listTags, resolveBlocks, restoreCategories, saveBlock, saveCategory, type ExampleImage } from '../api'
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
const emit = defineEmits<{ use: [LibraryBlock[]]; 'draft-saved': [LibraryBlock] }>()
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

// Restore built-in categories the user deleted — a dropdown listing every default, present ones disabled.
// Teleported to <body> and fixed-positioned so the narrow rail's overflow/stacking can't clip it.
const restoreOpen = ref(false)
const restoreBtn = ref<HTMLElement | null>(null)
const restorePos = ref({ top: 0, left: 0 })
const defaults = ref<{ slug: string; name: string; color: string }[]>([])
const restoreSel = ref<Set<string>>(new Set())
const presentSlugs = computed(() => new Set(categories.value.map((c) => c.slug)))
async function openRestore() {
  restoreSel.value = new Set()
  if (!defaults.value.length) {
    try { defaults.value = await defaultCategories() } catch (e) { push(e instanceof Error ? e.message : 'Failed', 'err'); return }
  }
  const r = restoreBtn.value?.getBoundingClientRect()
  if (r) {
    const w = 244 // popover width
    const navRight = document.querySelector('.sidebar')?.getBoundingClientRect().right ?? 0
    let left = Math.max(r.right - w, navRight + 4) // right-align to the button, but never over the nav
    left = Math.min(left, window.innerWidth - w - 8) // keep inside the viewport
    restorePos.value = { top: r.bottom + 4, left: Math.max(8, left) }
  }
  restoreOpen.value = true
}
function toggleRestore(slug: string) {
  const s = new Set(restoreSel.value)
  s.has(slug) ? s.delete(slug) : s.add(slug)
  restoreSel.value = s
}
async function doRestore() {
  if (!restoreSel.value.size) return
  try {
    const res = await restoreCategories([...restoreSel.value])
    restoreOpen.value = false
    await refreshAll()
    push(`Restored ${res.restored.length} categor${res.restored.length === 1 ? 'y' : 'ies'}`, 'ok')
  } catch (e) { push(e instanceof Error ? e.message : 'Restore failed', 'err') }
}

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

// ---- category management: one modal for create / edit (rename + recolor) / delete ----
const catModal = ref<{ mode: 'create' | 'edit'; slug: string | null; name: string; color: string; onCreated?: (slug: string) => void } | null>(null)
function openCreateCategory(onCreated?: (slug: string) => void) {
  catModal.value = { mode: 'create', slug: null, name: '', color: PALETTE[0], onCreated }
}
function openEditCategory(c: CategoryCount) {
  catModal.value = { mode: 'edit', slug: c.slug, name: c.name, color: c.color }
}
function closeCatModal() { catModal.value = null }
async function saveCatModal() {
  const m = catModal.value
  if (!m) return
  if (!m.name.trim()) { push('Category name is required', 'err'); return }
  try {
    // Edit keeps the same slug (rename only changes the label) — no accidental new category.
    const res = await saveCategory(m.name.trim(), m.color, m.mode === 'edit' ? (m.slug ?? undefined) : undefined)
    await loadCategories()
    const cb = m.onCreated
    catModal.value = null
    if (cb) cb(res.slug)
  } catch (e) {
    push(e instanceof Error ? e.message : 'Save failed', 'err')
  }
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
async function deleteFromCatModal() {
  const m = catModal.value
  if (!m || !m.slug) return
  const c = categories.value.find((x) => x.slug === m.slug)
  catModal.value = null
  if (c) await removeCategory(c)
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
      <!-- left rail — categories only (+ new / restore for now) -->
      <div class="rail">
        <div class="railhd">
          <span>Categories</span>
          <button ref="restoreBtn" class="railbtn" title="Restore default categories" @click="openRestore()">⟲</button>
          <button class="railbtn" title="New category" @click="openCreateCategory()">＋</button>
          <Teleport to="body">
            <template v-if="restoreOpen">
              <div class="restore-back" @click="restoreOpen = false"></div>
              <div class="restorepop" :style="{ top: restorePos.top + 'px', left: restorePos.left + 'px' }" @click.stop>
                <div class="rp-hd">Restore default categories</div>
                <div class="rp-list">
                  <label v-for="d in defaults" :key="d.slug" class="rp-item" :class="{ have: presentSlugs.has(d.slug) }">
                    <input type="checkbox" :disabled="presentSlugs.has(d.slug)"
                      :checked="presentSlugs.has(d.slug) || restoreSel.has(d.slug)" @change="toggleRestore(d.slug)" />
                    <span class="cdot" :style="{ background: d.color }"></span>
                    <span class="rp-name">{{ d.name }}</span>
                    <span v-if="presentSlugs.has(d.slug)" class="rp-tag">present</span>
                  </label>
                </div>
                <div class="rp-ft">
                  <button class="rp-cancel" @click="restoreOpen = false">Cancel</button>
                  <button class="rp-ok" :disabled="!restoreSel.size" @click="doRestore">Restore{{ restoreSel.size ? ` ${restoreSel.size}` : '' }}</button>
                </div>
              </div>
            </template>
          </Teleport>
        </div>
        <div class="catlist">
          <div class="catrow" :class="{ on: activeCategory === '' }" @click="selectCategory('')">
            <span class="cdot" style="background:var(--text-dim)"></span><span class="cn">All blocks</span><span class="cc">{{ allCount }}</span>
          </div>
          <div v-for="c in categories" :key="c.slug" class="catrow" :class="{ on: activeCategory === c.slug }"
            :style="{ '--cat': c.color }" @click="selectCategory(c.slug)">
            <span class="cdot editable" title="Edit category" @click.stop="openEditCategory(c)"></span>
            <span class="cn">{{ c.name }}</span>
            <button class="ce" title="Edit category" @click.stop="openEditCategory(c)">✎</button>
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

      <!-- block editor drawer -->
      <div v-if="editor" class="drawer">
        <div class="dhd">{{ editor.isNew ? '＋ New block' : '✎ Edit block' }}<span class="x" @click="closeEditor">✕</span></div>
        <div class="dbody">
          <div class="fld"><label>Name</label><input v-model="editor.block.name" placeholder="Block name" /></div>

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
          <div class="fld"><label>Example images</label>
            <div v-if="examples.length" class="examples">
              <img v-for="ex in examples" :key="ex.image_id" :src="`${ex.url}?w=400`" alt="example" loading="lazy"
                title="Click to enlarge" @click="lightbox = ex.url" />
            </div>
            <div v-else class="prev"><b>Inherited by images</b>Images generated with this block carry its tags automatically — the most-matching ones show up here.</div>
          </div>
        </div>
        <div class="dfoot">
          <button v-if="!editor.isNew" class="del" @click="removeBlock(editor.block)">Delete</button>
          <span class="sp"></span>
          <button class="cancel" @click="closeEditor">Cancel</button>
          <button class="save" :disabled="!canSaveBlock" @click="saveEditor">Save block</button>
        </div>
      </div>
    </div>

    <!-- one modal for all category management: create + edit (rename/recolor) + delete -->
    <Teleport to="body">
      <div v-if="catModal" class="catmodal-back" @click="closeCatModal">
        <div class="catmodal" @click.stop>
          <div class="cmhd">{{ catModal.mode === 'create' ? 'New category' : 'Edit category' }}</div>
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
            <button v-if="catModal.mode === 'edit' && catModal.slug !== 'custom'" class="del" @click="deleteFromCatModal">Delete</button>
            <span class="sp"></span>
            <button class="cancel" @click="closeCatModal">Cancel</button>
            <button class="save" @click="saveCatModal">Save</button>
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
.railhd{position:relative;display:flex;align-items:center;gap:2px;font-size:10px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:var(--text-faint);padding:12px 10px 6px 12px}
.railhd > span:first-child{margin-right:auto}
.railhd .railbtn{border:0;background:transparent;color:var(--text-faint);font-size:15px;line-height:1;cursor:pointer;padding:0 4px;border-radius:4px}
.railhd .railbtn:hover{color:var(--accent);background:var(--surface-3)}
.catlist{flex:1;min-height:0;overflow-y:auto;padding:4px 8px 10px}
.catrow{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:var(--radius);color:var(--text-dim);font-size:12.5px;font-weight:600;margin-bottom:1px;cursor:pointer}
.catrow .cdot{width:9px;height:9px;border-radius:50%;background:var(--cat,#738496);flex-shrink:0}
.catrow .cn{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.catrow .cc{font-size:11px;color:var(--text-faint);font-weight:500;font-variant-numeric:tabular-nums}
.catrow:hover{background:var(--surface-3);color:var(--text)}
.catrow.on{background:var(--nav-active);color:var(--accent)}
.catrow.on .cc{color:var(--accent)}
.catrow .cdot.editable{cursor:pointer}
.catrow .cdot.editable:hover{box-shadow:0 0 0 3px color-mix(in srgb,var(--cat,#738496) 35%,transparent)}
.catrow .ce{margin-left:auto;width:18px;height:18px;flex-shrink:0;border:0;background:transparent;color:var(--text-faint);font-size:11px;line-height:1;cursor:pointer;border-radius:3px;opacity:0;padding:0}
.catrow:hover .ce{opacity:1}
.catrow .ce:hover{color:var(--accent);background:var(--surface-3)}
.catrow .ce + .cc{margin-left:6px}

/* restore-defaults popover (teleported) */
.restore-back{position:fixed;inset:0;z-index:2100}
.restorepop{position:fixed;z-index:2101;width:244px;max-height:70vh;background:var(--surface-1);
  border:1px solid var(--border-strong);border-radius:var(--radius-lg);box-shadow:0 16px 40px rgba(0,0,0,.5);
  display:flex;flex-direction:column;text-transform:none;letter-spacing:0}
.rp-hd{font-size:12px;font-weight:600;color:var(--text-dim);padding:10px 12px 6px}
.rp-list{max-height:280px;overflow:auto;padding:0 6px 4px;display:flex;flex-direction:column;gap:1px}
.rp-item{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:6px;cursor:pointer;font-size:12.5px;font-weight:500;color:var(--text-dim)}
.rp-item:hover{background:var(--surface-3);color:var(--text)}
.rp-item.have{opacity:.5;cursor:default}.rp-item.have:hover{background:transparent}
.rp-item input{accent-color:var(--accent);cursor:inherit}
.rp-item .cdot{width:10px;height:10px;border-radius:3px;flex-shrink:0}
.rp-name{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rp-tag{font-size:9px;font-weight:700;text-transform:uppercase;color:var(--text-faint)}
.rp-ft{display:flex;gap:8px;justify-content:flex-end;padding:8px 12px;border-top:1px solid var(--border)}
.rp-ft button{border-radius:var(--radius);font:inherit;font-size:12px;font-weight:600;padding:6px 12px;cursor:pointer}
.rp-cancel{border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text-dim)}
.rp-cancel:hover{color:var(--text)}
.rp-ok{border:0;background:var(--accent);color:var(--on-accent)}
.rp-ok:disabled{opacity:.5;cursor:default}

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

/* block editor drawer */
.drawer{width:340px;flex-shrink:0;border-left:1px solid var(--border);background:var(--surface-1);display:flex;flex-direction:column}
.dhd{display:flex;align-items:center;gap:8px;padding:14px 16px;border-bottom:1px solid var(--border);font-weight:650;font-size:14px}
.dhd .x{margin-left:auto;color:var(--text-faint);font-size:16px;cursor:pointer}
.dbody{flex:1;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:15px}
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
.dfoot{display:flex;align-items:center;gap:8px;padding:12px 16px;border-top:1px solid var(--border)}
.dfoot .del{color:#e8913a;border:1px solid color-mix(in srgb,#b65c02 40%,var(--border));background:transparent;border-radius:var(--radius);padding:7px 11px;font-size:12px;font-weight:600;cursor:pointer}
.dfoot .sp{flex:1}
.dfoot .cancel{border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer}
.dfoot .save{border:0;background:var(--accent);color:#fff;border-radius:var(--radius);padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer}
.dfoot .save:disabled{opacity:.5;cursor:default}
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
</style>
