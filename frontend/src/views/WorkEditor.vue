<script setup lang="ts">
/* Full-page work view/editor (Works tab). One page, a View⇄Edit toggle and a Gallery⇄Composition facet
 * switch (design/works-tab-mockup.html). The Gallery facet renders the shared GalleryStack over the
 * loaded WorkDoc (readonly in View); edits mutate the WorkDoc in place and autosave. The Composition
 * facet fills in next. */
import { computed, nextTick, ref, watch } from 'vue'
import { ApiError, loadWork, saveWork } from '../api'
import { useToast } from '../composables/useToast'
import { useConfirm } from '../composables/useConfirm'
import { useImagePreview } from '../composables/useImagePreview'
import { useImageDownload } from '../composables/useImageDownload'
import GalleryStack from '../components/GalleryStack.vue'
import { gridsOf, purgeImages, removeIdFromGrids } from '../vault/workOps'
import type { ImageNodeData, WorkDoc, ZoneNode } from '../types'

const props = defineProps<{ workId: string; initialMode?: 'view' | 'edit' }>()
const emit = defineEmits<{ back: []; openInGenerate: [string] }>()
const { push } = useToast()
const { confirm } = useConfirm()
const { preview } = useImagePreview()

const doc = ref<WorkDoc | null>(null)
const loading = ref(true)
const ready = ref(false) // gate autosave until the initial load (+ heal) settles
const mode = ref<'view' | 'edit'>(props.initialMode ?? 'view')
const saveState = ref<'idle' | 'saving' | 'saved'>('idle')

async function load() {
  loading.value = true; ready.value = false
  mode.value = props.initialMode ?? 'view'
  saveState.value = 'idle'
  try {
    const d = await loadWork(props.workId)
    healGalleryBlocks(d) // seed a default album if the gallery zone lacks one (before autosave arms)
    doc.value = d
    await nextTick()
    ready.value = true
  } catch (e) {
    push(e instanceof Error ? e.message : 'Could not load work', 'err')
    emit('back')
  } finally {
    loading.value = false
  }
}
watch(() => props.workId, load, { immediate: true })

// ---- gallery model over the WorkDoc (mutated in place; the deep watch below autosaves) ----
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function galleryZoneOf(d: WorkDoc): any {
  return (d.canvas?.nodes || []).find((n) => n.type === 'zone' && (n.data as { role?: string })?.role === 'gallery')
}
function healGalleryBlocks(d: WorkDoc) {
  const zone = galleryZoneOf(d)
  if (zone && !Array.isArray(zone.data.blocks)) {
    const ids = (d.images || []).filter((im) => im.role === 'gallery')
      .sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || ''))).map((im) => im.id)
    zone.data.blocks = [{ id: 'gb-seed', type: 'grid', imageIds: ids, cols: 3 }]
  }
}
const galleryData = computed<ZoneNode['data'] | null>(() => (doc.value ? galleryZoneOf(doc.value)?.data ?? null : null))
const galleryImages = computed(() => {
  const d = doc.value
  if (!d) return []
  const snapById = new Map((d.snapshots || []).map((s) => [s.id, s]))
  return d.images.filter((im) => im.role === 'gallery').map((im) => {
    const snap = im.snapshot_id ? snapById.get(im.snapshot_id) : undefined
    const p = (snap?.params || {}) as { width?: number; height?: number }
    const data: Partial<ImageNodeData> = {
      url: `/api/vault/works/${d.id}/images/${im.id}`,
      ar: im.ar ?? (p.width && p.height ? p.width / p.height : undefined),
      favorite: !!im.favorite, tags: im.tags || [], created_at: im.created_at || '',
      snapshot: snap ? {
        components: snap.components, positive: snap.assembled_positive, negative: snap.assembled_negative,
        params: snap.params, hash: snap.hash, created_at: snap.created_at,
      } : undefined,
    }
    return { id: im.id, data }
  })
})
const galleryCount = () => galleryImages.value.length

// Album membership + deletion go through the shared workOps (same invariant as the canvas — no divergence).
const grids = () => gridsOf(galleryData.value?.blocks)
function onDropOnGrid(p: { gridId: string; imageId?: string }) {
  if (!p.imageId) return // draft payloads only exist in the Generate canvas, not here
  const grid = grids().find((g) => g.id === p.gridId)
  if (!grid) return
  removeIdFromGrids(galleryData.value?.blocks, p.imageId)
  if (!grid.imageIds.includes(p.imageId)) grid.imageIds.push(p.imageId)
}
function onDropOnQuick(p: { imageId?: string }) { if (p.imageId) removeIdFromGrids(galleryData.value?.blocks, p.imageId) }
function onToQuick(id: string) { removeIdFromGrids(galleryData.value?.blocks, id) }
function onFavorite(id: string) { const im = doc.value?.images.find((i) => i.id === id); if (im) im.favorite = !im.favorite }
async function onDeleteImg(id: string) {
  if (!(await confirm({ title: 'Delete image', danger: true, confirmLabel: 'Delete', message: 'Remove this image from the work? This cannot be undone.' }))) return
  if (doc.value) purgeImages(doc.value, [id])
}
async function onClearQuick(ids: string[]) {
  if (!ids.length) return
  if (!(await confirm({ title: 'Clear Quick access', danger: true, confirmLabel: `Delete ${ids.length}`,
    message: `Delete ${ids.length} unsorted image${ids.length === 1 ? '' : 's'} from the work? Favourited images are kept. This cannot be undone.` }))) return
  if (doc.value) purgeImages(doc.value, ids)
}

// ---- autosave: any WorkDoc mutation (gallery structure or images) → debounced save ----
// Coalesced onto ONE in-flight save: an edit landing mid-save re-runs after it, on the fresh base, so
// a slow save + fast typing never fires a second concurrent PUT that would 409 against our own save.
let saveTimer: ReturnType<typeof setTimeout> | null = null
let suppressWatch = false // set while writing the server-returned updated_at back (not a user edit)
let inflight: Promise<void> | null = null
let pending = false
watch(doc, () => { if (ready.value && !suppressWatch) markDirty() }, { deep: true }) // display prefs (cols/collapse/panels) persist from View too
function markDirty() {
  saveState.value = 'saving'
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => { void flush() }, 600)
}
async function flush() {
  if (!doc.value) return
  if (inflight) { pending = true; return } // a save is running → mark another needed, don't start a 2nd
  inflight = (async () => { do { pending = false; await saveOnce() } while (pending && doc.value) })()
  try { await inflight } finally { inflight = null }
}
async function saveOnce() {
  if (!doc.value) return
  try {
    // doc.updated_at is the base the editor loaded — the server 409s if the stored copy is newer (H3).
    const res = await saveWork(doc.value)
    saveState.value = 'saved'
    suppressWatch = true
    doc.value.updated_at = res.updated_at // new base for the next save — must not re-trigger autosave
    await nextTick(); suppressWatch = false
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) { pending = false; push('This work changed elsewhere — reloading.', 'err'); await load() }
    else { saveState.value = 'idle'; push(e instanceof Error ? e.message : 'Save failed', 'err') }
  }
}
function onTitleInput(v: string) { if (doc.value) doc.value.title = v } // deep watch autosaves

// ---- download (shared save-to-Downloads pipeline) ----
const { downloadUrls } = useImageDownload()
function downloadAll() { downloadUrls(galleryImages.value.map((im) => im.data.url || '').filter(Boolean)) }
</script>

<template>
  <div class="weditor">
    <div class="wbar">
      <button class="back" title="Back to Works" @click="emit('back')">←</button>
      <template v-if="doc">
        <input v-if="mode === 'edit'" class="wtin" :value="doc.title" placeholder="Untitled"
          @input="onTitleInput(($event.target as HTMLInputElement).value)" />
        <span v-else class="wt">{{ doc.title || 'Untitled' }}</span>
        <span class="wc">· {{ galleryCount() }} image{{ galleryCount() === 1 ? '' : 's' }}</span>
        <span class="saved" :class="saveState">{{ saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved' : '' }}</span>
      </template>
      <span v-else class="wt">Loading…</span>

      <span class="sp"></span>

      <div class="seg">
        <button :class="{ on: mode === 'view' }" @click="mode = 'view'">View</button>
        <button :class="{ on: mode === 'edit' }" @click="mode = 'edit'">Edit</button>
      </div>
      <button class="ic" title="Open in the Generate canvas" @click="emit('openInGenerate', workId)">↗ Generate</button>
      <button class="ic" title="Download all gallery images" @click="downloadAll">⤓</button>
    </div>

    <div v-if="loading" class="wstate">Loading work…</div>
    <div v-else-if="doc" class="wbody">
      <GalleryStack v-if="galleryData" :data="galleryData" :images="galleryImages" :readonly="mode === 'view'" embedded
        @favorite="onFavorite" @preview="preview" @to-quick="onToQuick" @delete-img="onDeleteImg"
        @clear-quick="onClearQuick" @download="downloadUrls" @drop-on-grid="onDropOnGrid" @drop-on-quick="onDropOnQuick" />
      <div v-else class="placeholder">This work has no gallery zone.</div>
    </div>
  </div>
</template>

<style scoped>
.weditor { flex: 1; min-width: 0; display: flex; flex-direction: column; overflow: hidden; }
.wbar { display: flex; align-items: center; gap: 10px; padding: 11px 18px; border-bottom: 1px solid var(--border); background: var(--surface-1); flex-shrink: 0; }
.wbar .back { border: 0; background: transparent; color: var(--text-faint); font-size: 17px; cursor: pointer; padding: 0 4px; }
.wbar .back:hover { color: var(--text); }
.wbar .wt { font-size: 15px; font-weight: 700; }
.wbar .wtin { font: inherit; font-size: 15px; font-weight: 700; color: var(--text); background: var(--surface-2); border: 1px solid var(--border); border-radius: 6px; padding: 3px 9px; outline: none; }
.wbar .wtin:focus { border-color: var(--accent); }
.wbar .wc { font-size: 12px; color: var(--text-faint); }
.wbar .sp { flex: 1; }
.seg { display: inline-flex; border: 1px solid var(--border-strong); border-radius: var(--radius); overflow: hidden; }
.seg button { border: 0; border-left: 1px solid var(--border); background: var(--surface-2); color: var(--text-dim); font: inherit; font-size: 12px; font-weight: 600; padding: 6px 13px; cursor: pointer; }
.seg button:first-child { border-left: 0; }
.seg button.on { background: var(--nav-active); color: var(--accent); }
.saved { font-size: 11.5px; color: var(--text-faint); min-width: 48px; display: inline-flex; align-items: center; gap: 5px; }
.saved.saved::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--ok); }
.ic { border: 1px solid var(--border-strong); background: var(--surface-2); color: var(--text-dim); border-radius: var(--radius); height: 30px; padding: 0 10px; font: inherit; font-size: 12px; font-weight: 600; cursor: pointer; }
.ic:hover { color: var(--accent); border-color: var(--accent); }
.wstate { padding: 40px; text-align: center; color: var(--text-faint); }
.wbody { flex: 1; min-height: 0; display: flex; }
.wbody > * { flex: 1; min-width: 0; }
.placeholder { display: flex; align-items: center; justify-content: center; color: var(--text-faint); font-size: 13px; padding: 40px; text-align: center; }
</style>
