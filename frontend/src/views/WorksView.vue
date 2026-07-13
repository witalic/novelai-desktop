<script setup lang="ts">
import { onActivated, onUnmounted, ref } from 'vue'
import { ApiError, deleteWork, listWorks, type WorkSort } from '../api'
import { useToast } from '../composables/useToast'
import { useConfirm } from '../composables/useConfirm'
import WorkEditor from './WorkEditor.vue'
import type { WorkListItem } from '../types'

const emit = defineEmits<{ open: [string] }>()

// The Works tab shows either the list or a single work's view/editor (full-bleed).
const openWork = ref<{ id: string; mode: 'view' | 'edit' } | null>(null)
function openEditor(id: string, mode: 'view' | 'edit') { openWork.value = { id, mode } }
function closeEditor() { openWork.value = null; fetchPage() } // returning may have changed titles/counts
const { push } = useToast()
const { confirm } = useConfirm()

const items = ref<WorkListItem[]>([])
const total = ref(0)
const page = ref(1)
const perPage = 24
const loading = ref(false)
const noVault = ref(false)

// ---- filter + sort (name search now; tag/content filters land with the DB indexing step) ----
const search = ref('')
const sort = ref<WorkSort>('updated')
const direction = ref<'asc' | 'desc'>('desc')
const sortOpen = ref(false)
const SORT_OPTS: { key: WorkSort; label: string }[] = [
  { key: 'updated', label: 'Last updated' },
  { key: 'created', label: 'Date created' },
  { key: 'name', label: 'Name' },
  { key: 'image_count', label: 'Image count' },
]
const sortLabel = () => SORT_OPTS.find((o) => o.key === sort.value)?.label ?? 'Last updated'
function setSort(key: WorkSort) {
  if (sort.value !== key) { sort.value = key; reload() }
  sortOpen.value = false
}
function toggleDir() { direction.value = direction.value === 'asc' ? 'desc' : 'asc'; reload() }

let searchTimer: ReturnType<typeof setTimeout> | null = null
function onSearch(v: string) {
  search.value = v
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(reload, 250) // debounce — don't hit the index on every keystroke
}
function reload() { requestedPage = 1; fetchPage(1) }

let fetchReq = 0
async function fetchPage(p = page.value) {
  const req = ++fetchReq // rapid pagination: a slow earlier response must not overwrite a newer page
  loading.value = true
  try {
    const res = await listWorks({ page: p, perPage, search: search.value.trim() || undefined, sort: sort.value, direction: direction.value })
    if (req !== fetchReq) return
    items.value = res.items
    total.value = res.total
    page.value = res.page
    requestedPage = res.page
    noVault.value = false
  } catch (e) {
    if (req !== fetchReq) return
    // 409 = no vault folder chosen yet — a normal first-run state, not an error.
    if (e instanceof ApiError && e.status === 409) {
      noVault.value = true
      items.value = []
      total.value = 0
    } else {
      push(e instanceof Error ? e.message : 'Could not load works', 'err')
    }
  } finally {
    if (req === fetchReq) loading.value = false
  }
}

const pages = () => Math.max(1, Math.ceil(total.value / perPage))
let requestedPage = 1 // the last page we asked for — dedup rapid clicks before the response updates page.value
function go(p: number) {
  if (p < 1 || p > pages() || p === requestedPage) return
  requestedPage = p
  fetchPage(p)
}

async function removeWork(w: WorkListItem) {
  const ok = await confirm({
    title: 'Delete work?', danger: true, confirmLabel: 'Delete',
    message: `Delete “${w.title || 'Untitled'}” and its images from disk? This can't be undone.`,
  })
  if (!ok) return
  try {
    await deleteWork(w.id)
    push('Work deleted', 'ok')
    // If the last item on a page is gone, step back a page.
    fetchPage(items.value.length === 1 && page.value > 1 ? page.value - 1 : page.value)
  } catch (e) {
    push(e instanceof Error ? e.message : 'Delete failed', 'err')
  }
}

// Refresh whenever the tab comes into view — onActivated also fires on first mount under KeepAlive, so a
// separate setup-time fetch would just double-load.
onActivated(() => fetchPage())
onUnmounted(() => { if (searchTimer) clearTimeout(searchTimer) })
</script>

<template>
  <WorkEditor v-if="openWork" :work-id="openWork.id" :initial-mode="openWork.mode"
    @back="closeEditor" @open-in-generate="(id) => emit('open', id)" />

  <div v-else class="works" @click="sortOpen = false">
    <div class="whead">
      <h1>Works</h1><span class="count">· {{ total }} saved</span>
    </div>

    <div class="wscroll">
      <div class="wtoolbar">
        <div class="search">
          <span class="ic">⌕</span>
          <input type="text" placeholder="Search by name…" :value="search" @input="onSearch(($event.target as HTMLInputElement).value)" />
        </div>
        <span class="hsp"></span>
        <div class="sortwrap">
          <button class="sortbtn" @click.stop="sortOpen = !sortOpen">Sort: {{ sortLabel() }}</button>
          <div v-if="sortOpen" class="sortmenu" @click.stop>
            <button v-for="o in SORT_OPTS" :key="o.key" class="srow" :class="{ on: sort === o.key }" @click="setSort(o.key)">{{ o.label }}</button>
          </div>
        </div>
        <button class="dirbtn" :title="direction === 'asc' ? 'Ascending' : 'Descending'" @click.stop="toggleDir">{{ direction === 'asc' ? '↑' : '↓' }}</button>
      </div>

      <div v-if="noVault" class="empty">
        <div class="emoji">▤</div>
        <p>No vault folder chosen yet. Pick one in <b>Settings</b> to start saving works to disk.</p>
      </div>

      <div v-else-if="!loading && !items.length && search.trim()" class="empty">
        <div class="emoji">⌕</div>
        <p>No works match “{{ search.trim() }}”.<br /><button class="linkbtn" @click="onSearch('')">Clear search</button></p>
      </div>

      <div v-else-if="!loading && !items.length" class="empty">
        <div class="emoji">✦</div>
        <p>No saved works yet. Build a composition in the Generate view — it auto-saves once you add a title or generate an image.</p>
      </div>

      <div v-else class="grid">
      <div v-for="w in items" :key="w.id" class="card" role="button" tabindex="0"
        @click="openEditor(w.id, 'view')" @keyup.enter="openEditor(w.id, 'view')">
        <div class="thumb">
          <img v-if="w.preview_url" :src="w.preview_url" :alt="w.title" loading="lazy" />
          <span v-else class="ph">✦</span>
          <div class="cardacts">
            <button class="cabtn" title="View" @click.stop="openEditor(w.id, 'view')">⤢</button>
            <button class="cabtn" title="Open in Generate" @click.stop="emit('open', w.id)">↗</button>
            <button class="cabtn del" title="Delete work" @click.stop="removeWork(w)">🗑</button>
          </div>
        </div>
        <div class="meta">
          <div class="title">{{ w.title || 'Untitled' }}</div>
          <div class="sub">{{ w.image_count }} image{{ w.image_count === 1 ? '' : 's' }}</div>
        </div>
      </div>
    </div>

      <footer v-if="pages() > 1" class="pager">
        <button :disabled="page <= 1" @click="go(page - 1)">‹ Prev</button>
        <span>Page {{ page }} / {{ pages() }}</span>
        <button :disabled="page >= pages()" @click="go(page + 1)">Next ›</button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.works { flex: 1; display: flex; flex-direction: column; min-width: 0; background: var(--bg); }
.whead { display: flex; align-items: center; gap: 8px; height: 55px; padding: 0 18px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.whead h1 { font-size: 18px; font-weight: 700; margin: 0; }
.whead .count { font-size: 13px; color: var(--text-faint); }
.wscroll { flex: 1; min-height: 0; overflow-y: auto; padding: 20px 24px; }
.wtoolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 20px; }
.hsp { flex: 1; }
.search { position: relative; }
.search input { width: 240px; font: inherit; font-size: 12.5px; color: var(--text); background: var(--surface-2);
  border: 1px solid var(--border); border-radius: var(--radius); padding: 7px 10px 7px 30px; outline: none; }
.search input:focus { border-color: var(--accent); }
.search .ic { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: var(--text-faint); font-size: 13px; pointer-events: none; }
.sortwrap { position: relative; }
.sortbtn { border: 1px solid var(--border-strong); background: var(--surface-2); color: var(--text-dim);
  border-radius: var(--radius); height: 32px; padding: 0 12px; font: inherit; font-size: 12.5px; font-weight: 600; cursor: pointer; }
.sortbtn:hover { color: var(--text); }
.sortmenu { position: absolute; right: 0; top: calc(100% + 5px); z-index: 20; min-width: 160px; padding: 5px;
  border: 1px solid var(--border-strong); border-radius: var(--radius-lg); background: var(--surface-1); box-shadow: 0 10px 30px rgba(0,0,0,.45); }
.srow { display: block; width: 100%; text-align: left; border: 0; background: transparent; color: var(--text);
  font: inherit; font-size: 12.5px; font-weight: 600; padding: 7px 9px; border-radius: var(--radius); cursor: pointer; }
.srow:hover { background: var(--surface-3); }
.srow.on { color: var(--accent); }
.dirbtn { width: 32px; height: 32px; border: 1px solid var(--border-strong); background: var(--surface-2);
  color: var(--text-dim); border-radius: var(--radius); font-size: 14px; cursor: pointer; }
.dirbtn:hover { color: var(--accent); border-color: var(--accent); }
.linkbtn { border: 0; background: transparent; color: var(--accent); cursor: pointer; font: inherit; font-size: 13px; padding: 0; }
.empty { max-width: 420px; margin: 12vh auto; text-align: center; color: var(--text-dim); }
.empty .emoji { font-size: 34px; color: var(--text-faint); margin-bottom: 12px; }
.empty p { font-size: 13px; line-height: 1.6; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 16px; }
.card { text-align: left; padding: 0; background: var(--surface-1); border: 1px solid var(--border);
  border-radius: var(--radius); overflow: hidden; cursor: pointer; transition: border-color .12s, transform .12s; }
.card:hover { border-color: var(--border-strong); transform: translateY(-2px); }
.thumb { position: relative; aspect-ratio: 3 / 4; background: var(--surface-3); display: flex; align-items: center; justify-content: center; }
.thumb img { width: 100%; height: 100%; object-fit: cover; }
.thumb .ph { font-size: 30px; color: var(--text-faint); }
.cardacts { position: absolute; top: 8px; right: 8px; display: flex; gap: 5px; opacity: 0; transition: opacity .12s; }
.card:hover .cardacts { opacity: 1; }
.cabtn { width: 28px; height: 28px; border: 1px solid rgba(255,255,255,.3); background: rgba(0,0,0,.55); color: #fff;
  border-radius: 6px; font-size: 13px; line-height: 1; cursor: pointer; display: flex; align-items: center; justify-content: center; }
.cabtn:hover { border-color: #fff; }
.cabtn.del:hover { border-color: var(--danger, #e2483d); color: var(--danger, #e2483d); }
.meta { padding: 10px 12px 12px; }
.title { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sub { font-size: 11px; color: var(--text-faint); margin-top: 3px; }
.pager { display: flex; align-items: center; justify-content: center; gap: 16px; margin-top: 26px;
  font-size: 13px; color: var(--text-dim); }
.pager button { padding: 6px 14px; border-radius: var(--radius); border: 1px solid var(--border);
  background: var(--surface-2); color: var(--text); cursor: pointer; }
.pager button:disabled { opacity: .45; cursor: default; }
.pager button:not(:disabled):hover { border-color: var(--border-strong); }
</style>
