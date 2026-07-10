<script setup lang="ts">
import { onActivated, ref } from 'vue'
import { ApiError, deleteWork, listWorks } from '../api'
import { useToast } from '../composables/useToast'
import { useConfirm } from '../composables/useConfirm'
import type { WorkListItem } from '../types'

const emit = defineEmits<{ open: [string] }>()
const { push } = useToast()
const { confirm } = useConfirm()

const items = ref<WorkListItem[]>([])
const total = ref(0)
const page = ref(1)
const perPage = 24
const loading = ref(false)
const noVault = ref(false)

async function fetchPage(p = page.value) {
  loading.value = true
  try {
    const res = await listWorks(p, perPage)
    items.value = res.items
    total.value = res.total
    page.value = res.page
    noVault.value = false
  } catch (e) {
    // 409 = no vault folder chosen yet — a normal first-run state, not an error.
    if (e instanceof ApiError && e.status === 409) {
      noVault.value = true
      items.value = []
      total.value = 0
    } else {
      push(e instanceof Error ? e.message : 'Could not load works', 'err')
    }
  } finally {
    loading.value = false
  }
}

const pages = () => Math.max(1, Math.ceil(total.value / perPage))
function go(p: number) {
  if (p < 1 || p > pages() || p === page.value) return
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
</script>

<template>
  <div class="works">
    <header class="head">
      <h1>Works</h1>
      <span class="count">{{ total }} saved</span>
    </header>

    <div v-if="noVault" class="empty">
      <div class="emoji">▤</div>
      <p>No vault folder chosen yet. Pick one in <b>Settings</b> to start saving works to disk.</p>
    </div>

    <div v-else-if="!loading && !items.length" class="empty">
      <div class="emoji">✦</div>
      <p>No saved works yet. Build a composition in the Generate view — it auto-saves once you add a title or generate an image.</p>
    </div>

    <div v-else class="grid">
      <div v-for="w in items" :key="w.id" class="card" role="button" tabindex="0"
        @click="emit('open', w.id)" @keyup.enter="emit('open', w.id)">
        <div class="thumb">
          <img v-if="w.preview_url" :src="w.preview_url" :alt="w.title" loading="lazy" />
          <span v-else class="ph">✦</span>
          <button class="del" title="Delete work" @click.stop="removeWork(w)">🗑</button>
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
</template>

<style scoped>
.works { flex: 1; overflow-y: auto; padding: 26px 32px; min-width: 0; }
.head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 22px; }
.head h1 { font-size: 20px; font-weight: 650; }
.count { font-size: 13px; color: var(--text-faint); }
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
.del { position: absolute; top: 6px; right: 6px; width: 26px; height: 26px; border: 0; border-radius: 7px;
  background: color-mix(in srgb, #000 55%, transparent); color: #fff; font-size: 12px; cursor: pointer;
  opacity: 0; transition: opacity .12s; }
.card:hover .del { opacity: 1; }
.del:hover { background: var(--danger, #e2483d); }
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
