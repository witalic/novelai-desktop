<script setup lang="ts">
import { computed, onActivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { ApiError, deleteBlock, deleteCategory, defaultCategories, listBlocks, listCategories, listExamples, listTags, restoreCategories, saveBlock, saveCategory, type ExampleImage } from '../api'
import { useToast } from '../composables/useToast'
import { useConfirm } from '../composables/useConfirm'
import LibraryImport from '../components/LibraryImport.vue'
import { newId } from '../vault/ids'
import type { CategoryCount, LibraryBlock, TagCount } from '../types'

const props = defineProps<{
  // A work-local canvas block being saved to the vault: open the editor drawer prefilled.
  draftBlock?: { block: LibraryBlock; nonce: number } | null
}>()
const emit = defineEmits<{ use: [LibraryBlock[]]; 'draft-saved': [LibraryBlock] }>()
const { push } = useToast()
const { confirm } = useConfirm()

const PALETTE = ['#6e5dc6', '#0c66e4', '#ae4787', '#1f845a', '#b65c02', '#12b5a6', '#d4537e', '#e2483d', '#2fb8c6', '#738496']

const categories = ref<CategoryCount[]>([])
const blocks = ref<LibraryBlock[]>([])
const total = ref(0)
const page = ref(1)
const perPage = 48
const activeCategory = ref('') // '' = all
const selectedTags = ref<string[]>([])
const tagOptions = ref<TagCount[]>([])
const tagSearch = ref('')
const search = ref('')
const loading = ref(false)
const noVault = ref(false)

const colorBySlug = computed(() => Object.fromEntries(categories.value.map((c) => [c.slug, c.color])))
const catColor = (slug: string) => colorBySlug.value[slug] || '#738496'
const catName = (slug: string) => categories.value.find((c) => c.slug === slug)?.name || slug
const allCount = computed(() => categories.value.reduce((n, c) => n + c.count, 0))
const pages = () => Math.max(1, Math.ceil(total.value / perPage))

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
async function loadBlocks() {
  const req = ++blocksReq // rapid category/tag/search changes: a slow earlier response must not overwrite a newer one
  loading.value = true
  try {
    const res = await listBlocks({ categories: activeCategory.value ? [activeCategory.value] : [], tags: selectedTags.value, search: search.value, page: page.value, perPage })
    if (req !== blocksReq) return // superseded
    blocks.value = res.items
    total.value = res.total
    noVault.value = false
  } catch (e) {
    if (req !== blocksReq) return
    if (e instanceof ApiError && e.status === 409) { noVault.value = true; blocks.value = []; total.value = 0 }
    else push(e instanceof Error ? e.message : 'Could not load blocks', 'err')
  } finally {
    if (req === blocksReq) loading.value = false
  }
}
async function refreshAll() {
  await loadCategories()
  await Promise.all([loadBlocks(), loadTags()])
}
// onActivated also fires on first mount under KeepAlive, so a separate setup-time call would double-load.
onActivated(refreshAll)

// Bulk import (modal). On success, reload so the imported blocks + any new categories show.
const importing = ref(false)
async function onImportDone() { importing.value = false; await refreshAll() }

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
  page.value = 1
  clearSelection()
  loadBlocks(); loadTags() // tag filter persists across category switches; only the tag *options* re-scope
}
function toggleTag(name: string) {
  const i = selectedTags.value.indexOf(name)
  if (i >= 0) selectedTags.value.splice(i, 1)
  else selectedTags.value.push(name)
  page.value = 1
  clearSelection()
  loadBlocks(); loadCategories() // re-count categories under the new tag filter
}
let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(search, () => { if (searchTimer) clearTimeout(searchTimer); page.value = 1; clearSelection(); searchTimer = setTimeout(loadBlocks, 300) })
function goPage(p: number) { if (p < 1 || p > pages() || p === page.value) return; page.value = p; clearSelection(); loadBlocks() }

// ---- multi-select (bulk use / delete) ----
const selected = ref<string[]>([])
const selectedBlocks = computed(() => blocks.value.filter((b) => selected.value.includes(b.id)))
function toggleSelect(id: string) {
  const i = selected.value.indexOf(id)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(id)
}
function clearSelection() { selected.value = [] }
const allSelected = computed(() => blocks.value.length > 0 && selected.value.length === blocks.value.length)
const someSelected = computed(() => selected.value.length > 0 && !allSelected.value)
function toggleAll() {
  if (allSelected.value) clearSelection()
  else selected.value = blocks.value.map((b) => b.id) // selects everything in the current view
}
function useSelected() {
  if (selectedBlocks.value.length) emit('use', selectedBlocks.value)
  clearSelection()
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

const tagMatches = computed(() => {
  const q = tagSearch.value.trim().toLowerCase()
  return tagOptions.value.filter((t) => !q || t.name.toLowerCase().includes(q))
})

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

// ---- rail dropdowns (tag filter) + block-editor category selector: click-to-open, close outside ----
const tagFilterOpen = ref(false)
const tagselEl = ref<HTMLElement | null>(null)
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
  if (tagselEl.value && !tagselEl.value.contains(t)) tagFilterOpen.value = false
  if (blockCatEl.value && !blockCatEl.value.contains(t)) blockCatOpen.value = false
}
onMounted(() => document.addEventListener('mousedown', onDocPointer))
onUnmounted(() => {
  document.removeEventListener('mousedown', onDocPointer)
  if (searchTimer) clearTimeout(searchTimer) // don't let a debounced load fire after teardown
  if (exTimer) clearTimeout(exTimer)
  activeRailCleanup?.() // tear down a splitter drag if we unmount mid-drag
})

// The tag dropdown is fixed-positioned (measured off the input) so a resizable/scrolling rail can't clip it.
const tagDropStyle = ref<Record<string, string>>({})
function openTagFilter(e: FocusEvent) {
  const r = (e.target as HTMLElement).getBoundingClientRect()
  tagDropStyle.value = { left: `${r.left}px`, top: `${r.bottom + 5}px`, width: `${r.width}px` }
  tagFilterOpen.value = true
}

// Resizable split between the Categories pane and the Filter-by-tags pane (each scrolls independently).
// Default 65/35 (catsH === null → flex-basis); once the user drags, an explicit px height sticks.
const railEl = ref<HTMLElement | null>(null)
const catsPaneEl = ref<HTMLElement | null>(null)
const catsH = ref<number | null>(null)
function startRailDrag(e: MouseEvent) {
  e.preventDefault()
  const startY = e.clientY
  const startH = catsPaneEl.value?.getBoundingClientRect().height ?? 300
  const railH = railEl.value?.clientHeight ?? 600
  const onMove = (ev: MouseEvent) => { catsH.value = Math.max(96, Math.min(railH - 110, startH + (ev.clientY - startY))) }
  const onUp = () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); activeRailCleanup = null }
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
  activeRailCleanup = onUp // so an unmount mid-drag still tears these window listeners down
}
let activeRailCleanup: (() => void) | null = null

// ---- block editor drawer ----
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
    <div class="lhead">
      <h1>Library</h1><span class="count">· {{ allCount }} blocks</span>
      <div class="spacer"></div>
      <input class="search" v-model="search" placeholder="Search name or text…" />
      <button class="impbtn" @click="importing = true"><span>⭳</span> Import</button>
      <button class="newbtn" @click="openNew"><span>＋</span> New block</button>
    </div>

    <div v-if="noVault" class="empty">
      <div class="emoji">❏</div>
      <p>No vault folder chosen yet. Pick one in <b>Settings</b> to start building your prompt library.</p>
    </div>

    <div v-else class="lbody">
      <!-- category + tag rail: two independently-scrolling panes, resizable via the splitter -->
      <div class="rail" ref="railEl">
        <div class="rail-pane cats" :class="{ flexed: catsH === null }"
          :style="catsH !== null ? { height: catsH + 'px' } : undefined" ref="catsPaneEl">
          <div class="railhead">
            <span>Categories</span>
            <button ref="restoreBtn" class="addcat" title="Restore default categories" @click="openRestore()">⟲</button>
            <button class="addcat" title="New category" @click="openCreateCategory()">＋</button>
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
          <div class="catrow" :class="{ on: activeCategory === '' }" @click="selectCategory('')">
            <span class="cdot" style="background:var(--text-dim)"></span><span class="cn">All blocks</span><span class="cc">{{ allCount }}</span>
          </div>
          <div v-for="c in categories" :key="c.slug" class="catrow" :class="{ on: activeCategory === c.slug }"
            :style="{ '--cat': c.color }" @click="selectCategory(c.slug)">
            <span class="cdot editable" title="Edit category" @click.stop="openEditCategory(c)"></span>
            <span class="cn">{{ c.name }}</span>
            <button class="catedit" title="Edit category" @click.stop="openEditCategory(c)">✎</button>
            <span class="cc">{{ c.count }}</span>
          </div>
        </div>

        <div class="rail-split" title="Drag to resize" @mousedown="startRailDrag"></div>

        <div class="rail-pane tags" ref="tagselEl">
          <div class="railgroup">Filter by tags</div>
          <div class="tsel-field">
            <input class="tsel-input" v-model="tagSearch" placeholder="Search tags…" @focus="openTagFilter" />
            <div v-if="tagFilterOpen" class="tsel-drop" :style="tagDropStyle">
              <div v-for="t in tagMatches" :key="t.name" class="tsopt" :class="{ on: selectedTags.includes(t.name) }" @click="toggleTag(t.name)">
                <span class="ck">{{ selectedTags.includes(t.name) ? '✓' : '' }}</span><span class="cn">{{ t.name }}</span><span class="cc">{{ t.count }}</span>
              </div>
              <div v-if="!tagMatches.length" class="tshint">No tags {{ activeCategory ? `in ${catName(activeCategory)}` : 'yet' }}.</div>
              <div v-else class="tshint">Tags within <b>{{ activeCategory ? catName(activeCategory) : 'all categories' }}</b></div>
            </div>
          </div>
          <div v-if="selectedTags.length" class="tsel-chosen">
            <span v-for="t in selectedTags" :key="t" class="et">{{ t }} <b @click="toggleTag(t)">✕</b></span>
          </div>
        </div>
      </div>

      <!-- block grid -->
      <div class="gridwrap">
        <div v-if="blocks.length" class="selbar" :class="{ active: selected.length }">
          <button class="selall" @click="toggleAll">
            <span class="box" :class="{ on: allSelected, some: someSelected }">{{ allSelected ? '✓' : someSelected ? '–' : '' }}</span>
            {{ allSelected ? 'Deselect all' : 'Select all' }}
          </button>
          <span v-if="selected.length" class="scount">· {{ selected.length }} selected</span>
          <div class="ssp"></div>
          <template v-if="selected.length">
            <button class="sbtn use" @click="useSelected">⇢ Use</button>
            <button class="sbtn del" @click="deleteSelected">🗑 Delete</button>
            <button class="sbtn ghost" @click="clearSelection">Clear</button>
          </template>
        </div>
        <div v-if="!loading && !blocks.length" class="gridempty">
          <p>No blocks here yet. Click <b>＋ New block</b> to author one.</p>
        </div>
        <div class="grid">
          <div v-for="b in blocks" :key="b.id" class="bcard" :class="{ sel: selected.includes(b.id) }"
            :style="{ '--cat': catColor(b.category) }" @click="toggleSelect(b.id)">
            <div class="top">
              <span class="bd"></span><span class="bn">{{ b.name || 'Untitled' }}</span>
              <span class="polbadge" :class="{ neg: b.polarity === 'negative' }">{{ b.polarity === 'negative' ? '−' : '＋' }}</span>
            </div>
            <div class="catlbl">{{ catName(b.category) }}</div>
            <div class="btext">{{ b.text || 'empty' }}</div>
            <div class="btags"><span v-for="t in b.tags" :key="t" class="btag">{{ t }}</span></div>
            <div class="acts">
              <button class="act use" @click.stop="emit('use', [b])">⇢ Use</button>
              <button class="act" @click.stop="openEdit(b)">✎ Edit</button>
              <button class="act del" @click.stop="removeBlock(b)" title="Delete">🗑</button>
            </div>
          </div>
        </div>
        <footer v-if="pages() > 1" class="pager">
          <button :disabled="page <= 1" @click="goPage(page - 1)">‹ Prev</button>
          <span>Page {{ page }} / {{ pages() }}</span>
          <button :disabled="page >= pages()" @click="goPage(page + 1)">Next ›</button>
        </footer>
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
.lhead{display:flex;align-items:center;gap:12px;padding:13px 20px;border-bottom:1px solid var(--border);flex-shrink:0}
.lhead h1{font-size:16px;font-weight:650;margin:0}
.lhead .count{font-size:12px;color:var(--text-faint)}
.lhead .spacer{flex:1}
.search{width:230px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:7px 10px;color:var(--text);font:inherit;font-size:13px;outline:none}
.search:focus{border-color:var(--accent)}
.newbtn{background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--radius);font-weight:600;font-size:13px;padding:8px 14px;display:flex;align-items:center;gap:7px;cursor:pointer}
.impbtn{background:var(--surface-2);color:var(--text-dim);border:1px solid var(--border-strong);border-radius:var(--radius);font-weight:600;font-size:13px;padding:8px 14px;display:flex;align-items:center;gap:7px;cursor:pointer}
.impbtn:hover{color:var(--text)}

.empty{max-width:420px;margin:12vh auto;text-align:center;color:var(--text-dim)}
.empty .emoji{font-size:34px;color:var(--text-faint);margin-bottom:12px}
.empty p{font-size:13px;line-height:1.6}

.lbody{flex:1;display:flex;min-height:0}
.rail{width:200px;flex-shrink:0;border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.rail-pane{padding:8px 10px}
.rail-pane.cats{flex-shrink:0;overflow-y:auto}
.rail-pane.cats.flexed{flex:0 0 65%}
.rail-pane.tags{flex:1;min-height:64px;overflow-y:auto}
.rail-split{height:9px;flex-shrink:0;cursor:row-resize;position:relative}
.rail-split::before{content:"";position:absolute;left:10px;right:10px;top:4px;height:1px;background:var(--border)}
.rail-split:hover::before{background:var(--accent);height:2px;top:3px}
.railgroup{font-size:11px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint);padding:4px 8px 6px}
.railhead{position:relative;display:flex;align-items:center;gap:2px;font-size:11px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint);padding:4px 6px 6px 8px}
.railhead > span:first-child{margin-right:auto}
.railhead .addcat{border:0;background:transparent;color:var(--text-faint);font-size:15px;line-height:1;cursor:pointer;padding:0 4px;border-radius:4px}
.railhead .addcat:hover{color:var(--accent);background:var(--surface-3)}
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
.catrow{display:flex;align-items:center;gap:9px;padding:7px 9px;border-radius:var(--radius);color:var(--text-dim);font-size:13px;font-weight:500;margin-bottom:1px;cursor:pointer}
.catrow .cdot{width:8px;height:8px;border-radius:2px;background:var(--cat,#738496);flex-shrink:0}
.catrow .cn{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.catrow .cc{font-size:11px;color:var(--text-faint)}
.catrow:hover{background:var(--surface-2)}
.catrow.on{background:var(--nav-active);color:var(--text)}
.catrow.on .cc{color:var(--accent)}
.catrow .cdot.editable{cursor:pointer}
.catrow .cdot.editable:hover{box-shadow:0 0 0 3px color-mix(in srgb,var(--cat,#738496) 35%,transparent)}
.catrow .catedit{width:18px;height:18px;flex-shrink:0;border:0;background:transparent;color:var(--text-faint);font-size:12px;line-height:1;cursor:pointer;border-radius:3px;opacity:0;padding:0}
.catrow:hover .catedit{opacity:.8}
.catrow .catedit:hover{color:var(--accent);background:var(--surface-3);opacity:1}

.tagsel{padding:2px 6px}
.tsel-field{position:relative}
.tsel-input{width:100%;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:6px 9px;color:var(--text);font:inherit;font-size:12px;outline:none}
.tsel-input:focus{border-color:var(--accent)}
.tsel-chosen{display:flex;flex-wrap:wrap;gap:5px;margin:7px 0 0}
.et{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:var(--accent);background:var(--nav-active);border:1px solid color-mix(in srgb,var(--accent) 30%,transparent);border-radius:20px;padding:2px 8px}
.et b{color:var(--text-faint);font-weight:400;cursor:pointer}
.tsel-drop{position:fixed;z-index:30;background:var(--surface-2);border:1px solid var(--border-strong);border-radius:var(--radius);overflow:hidden;max-height:280px;overflow-y:auto;box-shadow:0 8px 24px rgba(0,0,0,.45)}
.tsopt{display:flex;align-items:center;gap:8px;padding:6px 9px;font-size:12px;color:var(--text-dim);cursor:pointer}
.tsopt:hover{background:var(--surface-3)}
.tsopt .ck{width:13px;height:13px;border:1px solid var(--border-strong);border-radius:3px;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:10px;color:#fff}
.tsopt.on .ck{background:var(--accent);border-color:var(--accent)}
.tsopt .cn{flex:1}
.tsopt .cc{font-size:11px;color:var(--text-faint)}
.tsopt.create{color:var(--accent);font-weight:600;border-top:1px dashed var(--border)}
.tshint{font-size:10.5px;color:var(--text-faint);padding:6px 8px 2px}

.gridwrap{flex:1;overflow:auto;padding:18px;display:flex;flex-direction:column}
.gridempty{margin:10vh auto;color:var(--text-dim);font-size:13px}
.selbar{display:flex;align-items:center;gap:10px;margin-bottom:14px;padding:7px 12px;background:var(--surface-1);
  border:1px solid var(--border);border-radius:var(--radius);position:sticky;top:0;z-index:4}
.selbar.active{background:var(--nav-active);border-color:color-mix(in srgb,var(--accent) 35%,var(--border))}
.selbar .selall{display:flex;align-items:center;gap:8px;border:0;background:transparent;color:var(--text-dim);font:inherit;font-size:12.5px;font-weight:600;cursor:pointer;padding:0}
.selbar .selall:hover{color:var(--text)}
.selbar .selall .box{width:15px;height:15px;border:1.5px solid var(--border-strong);border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:11px;color:#fff;line-height:1}
.selbar .selall .box.on,.selbar .selall .box.some{background:var(--accent);border-color:var(--accent)}
.selbar .scount{font-size:12.5px;font-weight:600;color:var(--accent)}
.selbar .ssp{flex:1}
.selbar .sbtn{border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text);border-radius:var(--radius);
  font-size:12px;font-weight:600;padding:6px 12px;cursor:pointer}
.selbar .sbtn.use{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,var(--border))}
.selbar .sbtn.del{color:#e8913a;border-color:color-mix(in srgb,#b65c02 40%,var(--border))}
.selbar .sbtn.ghost{border-color:transparent;background:transparent;color:var(--text-dim)}
.selbar .sbtn:hover{filter:brightness(1.15)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(232px,1fr));gap:14px;align-content:start}
.bcard{height:186px;background:var(--surface-1);border:1px solid var(--border);border-left:3px solid var(--cat,#738496);border-radius:9px;padding:12px 13px;display:flex;flex-direction:column;gap:8px;cursor:pointer;transition:border-color .1s,box-shadow .1s}
.bcard:hover{border-color:var(--border-strong)}
.bcard.sel{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 55%,transparent)}
.bcard .top{display:flex;align-items:center;gap:8px}
.bcard .bd{width:8px;height:8px;border-radius:50%;background:var(--cat,#738496);flex-shrink:0}
.bcard .bn{font-size:13px;font-weight:650;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bcard .polbadge{font-size:10px;font-weight:700;color:var(--text-faint);border:1px solid var(--border-strong);border-radius:4px;padding:0 5px;line-height:16px}
.bcard .polbadge.neg{color:#e8913a}
.bcard .catlbl{font-size:10.5px;color:var(--text-faint);text-transform:uppercase;letter-spacing:.3px;font-weight:600}
.bcard .btext{font-size:11.5px;color:var(--text-dim);line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:34px}
.bcard .btags{flex:1;min-height:0;display:flex;flex-wrap:wrap;gap:5px;align-content:flex-start;overflow-y:auto}
.bcard .btag{font-size:10.5px;color:var(--accent);background:var(--nav-active);border-radius:20px;padding:1px 8px}
.bcard .acts{display:flex;gap:6px;border-top:1px solid var(--border);padding-top:9px}
.bcard .act{flex:1;display:flex;align-items:center;justify-content:center;gap:5px;font-size:11.5px;font-weight:600;color:var(--text-dim);border:1px solid var(--border);border-radius:var(--radius);padding:5px;background:var(--surface-2);cursor:pointer}
.bcard .act:hover{color:var(--text);border-color:var(--border-strong)}
.bcard .act.use{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,var(--border));background:color-mix(in srgb,var(--accent) 10%,transparent)}
.bcard .act.del{flex:0 0 34px}

.pager{display:flex;align-items:center;justify-content:center;gap:16px;margin-top:22px;font-size:13px;color:var(--text-dim)}
.pager button{padding:6px 14px;border-radius:var(--radius);border:1px solid var(--border);background:var(--surface-2);color:var(--text);cursor:pointer}
.pager button:disabled{opacity:.45;cursor:default}

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
.accd{margin-top:5px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;max-height:180px;overflow-y:auto}
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
