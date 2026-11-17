<script setup lang="ts">
import { computed, onActivated, onUnmounted, reactive, ref, watch } from 'vue'
import { ApiError, downloadImages, galleryFacets, galleryImage, listGallery, setImageFavorite } from '../api'
import DateMenu from '../components/DateMenu.vue'
import FilterMenu from '../components/FilterMenu.vue'
import GalleryGrid from '../components/GalleryGrid.vue'
import TagPanel from '../components/TagPanel.vue'
import TagToggle from '../components/TagToggle.vue'
import { onAgentNotice } from '../composables/useAgentNotices'
import { modelLabel, useCatalog } from '../composables/useCatalog'
import { useImagePreview, type ViewerItem } from '../composables/useImagePreview'
import { useToast } from '../composables/useToast'
import { useVault, vaultMayBeAway } from '../composables/useVault'
import { imageMeta } from '../canvas/imageMeta'
import { galleryParams, NO_FILTERS, periodLabel, type GalleryFilters, type Period } from '../gallery/query'
import { categoryColors, placeLabel, recipeOf } from '../vault/recipe'
import type { GalleryFacets, GalleryImage, PersistedComponent } from '../types'

// Every image in the vault in one place, newest first (design/global-gallery.html) — a view over the works, with no
// state of its own: filters, a viewer with each image's recipe, its star, its work, its recipe sent to Generate.
const emit = defineEmits<{
  'open-image': [{ workId: string; imageId: string }]
  recipe: [{ components: PersistedComponent[]; params: Record<string, unknown> }]
}>()
const { push } = useToast()
const { openViewer } = useImagePreview()
useCatalog() // model names for the Model filter and the recipe
const { unreachable: vaultAway, onBack: onVaultBack } = useVault()
onUnmounted(onVaultBack(() => reload()))

const f = reactive<GalleryFilters>({ ...NO_FILTERS, tags: [], works: [], models: [] })
const items = ref<GalleryImage[]>([])
const total = ref(0)
const next = ref<string | null>(null)
const complete = ref(false)
const loading = ref(false)
const noVault = ref(false)
const facets = ref<GalleryFacets | null>(null)
const tagsOpen = ref(false)
const sortOpen = ref(false)
const stale = ref(false) // the vault changed (an agent) since this list was read
const listKey = ref(0) // a new list starts its grid at the top

// ---- reading: the first page and the facets together; more pages as the grid (or the viewer) nears the end ----
let token = 0
async function reload() {
  const mine = ++token
  const query = galleryParams(f)
  stale.value = false
  loading.value = true
  try {
    const [page, offered] = await Promise.all([listGallery(query, { order: f.order }), galleryFacets(query)])
    if (mine !== token) return
    items.value = page.items
    total.value = page.total
    next.value = page.next
    complete.value = !page.next
    facets.value = offered
    noVault.value = false
    listKey.value += 1
  } catch (e) {
    if (mine !== token) return
    items.value = []
    total.value = 0
    complete.value = true
    facets.value = null
    if (e instanceof ApiError && e.status === 409) noVault.value = true // no vault folder chosen yet
    else if (!vaultMayBeAway(e)) push(e instanceof Error ? e.message : 'Could not load the gallery', 'err')
  } finally {
    if (mine === token) loading.value = false
  }
}
let paging: Promise<void> | null = null
function more() {
  if (paging || complete.value || !next.value) return
  const mine = token
  paging = listGallery(galleryParams(f), { order: f.order, after: next.value }).then((page) => {
    if (mine !== token) return
    const seen = new Set(items.value.map((it) => it.image_id))
    items.value = [...items.value, ...page.items.filter((it) => !seen.has(it.image_id))]
    next.value = page.next
    complete.value = !page.next
  }).catch(() => { /* the next nearing of the end asks again */ }).finally(() => { paging = null })
}

// ---- filters ----
let searchTimer: ReturnType<typeof setTimeout> | null = null
function onSearch(v: string) {
  f.q = v
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(reload, 250) // debounce — don't hit the index on every keystroke
}
onUnmounted(() => { if (searchTimer) clearTimeout(searchTimer) })
function toggleIn(list: string[], v: string) {
  const i = list.indexOf(v)
  if (i >= 0) list.splice(i, 1)
  else list.push(v)
  reload()
}
function setPeriod(p: Period) { f.period = p; reload() }
function setOrder(o: GalleryFilters['order']) { sortOpen.value = false; if (f.order !== o) { f.order = o; reload() } }
function clearAll() {
  Object.assign(f, { ...NO_FILTERS, tags: [], works: [], models: [], unsorted: f.unsorted, order: f.order })
  reload()
}
const workOptions = computed(() => (facets.value?.works ?? []).map((w) => ({ value: w.id, label: w.title, count: w.count })))
const modelOptions = computed(() => (facets.value?.models ?? []).map((m) => ({ value: m.name, label: modelLabel(m.name), count: m.count })))
const workTitle = (id: string) => facets.value?.works.find((w) => w.id === id)?.title ?? 'a work'

// The row under the toolbar: what is shown, each filter going with one ✕.
const active = computed(() => {
  const out: { key: string; label: string; drop: () => void }[] = []
  if (f.q.trim()) out.push({ key: 'q', label: `“${f.q.trim()}”`, drop: () => onSearch('') })
  for (const t of f.tags) out.push({ key: `t:${t}`, label: `tag ${t}`, drop: () => toggleIn(f.tags, t) })
  for (const w of f.works) out.push({ key: `w:${w}`, label: workTitle(w), drop: () => toggleIn(f.works, w) })
  for (const m of f.models) out.push({ key: `m:${m}`, label: modelLabel(m), drop: () => toggleIn(f.models, m) })
  if (f.favorite) out.push({ key: 'fav', label: 'favourites', drop: () => { f.favorite = false; reload() } })
  if (f.period.kind !== 'any') out.push({ key: 'date', label: periodLabel(f.period).toLowerCase(), drop: () => setPeriod({ kind: 'any' }) })
  return out
})
const countLabel = computed(() => {
  const n = total.value.toLocaleString()
  if (active.value.length) return `${n} image${total.value === 1 ? '' : 's'} found`
  return f.unsorted ? `${n} image${total.value === 1 ? '' : 's'}` : `${n} image${total.value === 1 ? '' : 's'} in albums`
})
const unsortedHidden = computed(() => (!f.unsorted && facets.value ? facets.value.unsorted : 0))

// ---- an image: its star, the viewer with its recipe, its work, its recipe to Generate ----
async function toggleFavorite(it: GalleryImage) {
  const was = it.favorite
  it.favorite = !was // shown at once; the open copy of its work catches up through the backend's notice
  try { await setImageFavorite(it.work_id, it.image_id, !was) } catch (e) {
    it.favorite = was
    push(e instanceof Error ? e.message : 'Could not update the image', 'err')
  }
}
const byId = (id: string) => items.value.find((it) => it.image_id === id)
async function sendRecipe(it: GalleryImage) {
  try {
    const d = await galleryImage(it.work_id, it.image_id)
    if (!d.components.length) { push('This image has no recipe to send', 'err'); return }
    emit('recipe', { components: d.components, params: d.params })
  } catch (e) { push(e instanceof Error ? e.message : 'Could not read the recipe', 'err') }
}
async function download(it: GalleryImage) {
  try {
    const { count } = await downloadImages([{ work_id: it.work_id, image_id: it.image_id }])
    push(count ? 'Saved the image to Downloads' : 'The image couldn’t be written to Downloads', count ? 'ok' : 'err')
  } catch (e) { push(e instanceof Error ? e.message : 'Download failed', 'err') }
}
function open(start: GalleryImage) {
  const toItem = (it: GalleryImage): ViewerItem => ({ id: it.image_id, url: it.url, favorite: it.favorite,
    meta: [it.work_title, imageMeta(undefined, it.created_at)].filter(Boolean).join(' · ') })
  const act = (fn: (it: GalleryImage) => unknown) => (v: ViewerItem) => { const it = byId(v.id); if (it) fn(it) }
  openViewer({
    source: 'Gallery',
    items: () => items.value.map(toItem),
    more,
    recipe: async (v) => {
      const it = byId(v.id)
      if (!it) return null
      const [d, color] = await Promise.all([galleryImage(it.work_id, it.image_id), categoryColors()])
      return d.positive || d.components.length
        ? recipeOf({ positive: d.positive, negative: d.negative, components: d.components, params: d.params,
          tags: [...d.tags, ...d.block_tags] }, { work: d.work_title, where: placeLabel(d.album), colorOf: color })
        : null
    },
    actions: [
      { label: (v) => (v.favorite ? 'Unfavorite' : 'Favorite'), icon: '★', key: 'f', active: (v) => !!v.favorite,
        run: act(toggleFavorite) },
      { label: 'Recipe to Generate', icon: '↺', primary: true, closes: true, run: act(sendRecipe) },
      { label: 'Go to work', icon: '↗', closes: true, run: act((it) => emit('open-image', { workId: it.work_id, imageId: it.image_id })) },
      { label: 'Download', icon: '⤓', run: act(download) },
    ],
  }, start.image_id)
}

// ---- keeping up: shown → read; an agent's change while shown → offer a refresh (the list never jumps by itself) ----
let read = false
onActivated(() => {
  if (!read || stale.value) { read = true; reload() }
})
onAgentNotice((n) => {
  // Shown: the Refresh line offers the new state. Away: it is read again on the way back.
  if (!n.local && (n.kind === 'work' || n.kind === 'work_deleted') && read) stale.value = true
})
watch(vaultAway, (away) => { if (away) { items.value = []; facets.value = null } })
</script>

<template>
  <div class="gallery" @click="sortOpen = false">
    <div class="ghead">
      <h1>Gallery</h1><span class="count">· {{ countLabel }}</span>
      <span class="hsp"></span>
      <div class="sortwrap">
        <button class="sortbtn" @click.stop="sortOpen = !sortOpen">Sort: {{ f.order === 'newest' ? 'Newest first' : 'Oldest first' }} <span class="car">▾</span></button>
        <div v-if="sortOpen" class="sortmenu" @click.stop>
          <button class="srow" :class="{ on: f.order === 'newest' }" @click="setOrder('newest')">Newest first</button>
          <button class="srow" :class="{ on: f.order === 'oldest' }" @click="setOrder('oldest')">Oldest first</button>
        </div>
      </div>
    </div>

    <template v-if="vaultAway">
      <div class="empty"><div class="emoji away">⚠</div>
        <p><b>Your images are in a vault that can't be reached.</b><br />They show up here again as soon as it's back.</p></div>
    </template>
    <template v-else-if="noVault">
      <div class="empty"><div class="emoji">▤</div>
        <p>No vault folder chosen yet. Pick one in <b>Settings</b> to start saving works to disk.</p></div>
    </template>
    <template v-else>
      <div class="gtools">
        <div class="search">
          <span class="ic">⌕</span>
          <input type="text" placeholder="Search prompts, descriptions, sections…" :value="f.q"
            @input="onSearch(($event.target as HTMLInputElement).value)" />
        </div>
        <FilterMenu label="Works" noun="work" searchable :options="workOptions" :selected="f.works"
          @toggle="(v) => toggleIn(f.works, v)" @clear="f.works.splice(0); reload()" />
        <FilterMenu label="Model" noun="model" :options="modelOptions" :selected="f.models"
          @toggle="(v) => toggleIn(f.models, v)" @clear="f.models.splice(0); reload()" />
        <DateMenu :period="f.period" @pick="setPeriod" />
        <button class="tbtn" :class="{ on: f.favorite }" :aria-pressed="f.favorite" @click="f.favorite = !f.favorite; reload()">
          <span class="st">★</span> Favourites
        </button>
        <button class="sw" :class="{ on: f.unsorted }" role="switch" :aria-checked="f.unsorted" @click="f.unsorted = !f.unsorted; reload()">
          <span class="tr"></span>Include unsorted
        </button>
        <TagToggle :open="tagsOpen" :picked="f.tags.length" @toggle="tagsOpen = !tagsOpen" />
      </div>
      <TagPanel class="gtags" :tags="facets?.tags ?? []" :selected="f.tags" :open="tagsOpen"
        @toggle="(t) => toggleIn(f.tags, t)" @clear="f.tags.splice(0); reload()" />

      <div v-if="active.length || unsortedHidden || stale" class="ginfo">
        <div v-if="active.length" class="sum">
          <span>{{ total.toLocaleString() }} image{{ total === 1 ? '' : 's' }}</span>
          <span v-for="a in active" :key="a.key" class="fchip">{{ a.label }}<button :title="`Stop filtering by ${a.label}`" @click="a.drop">✕</button></span>
          <button class="linkbtn" @click="clearAll">Clear filters</button>
        </div>
        <div v-if="unsortedHidden && items.length" class="hint">
          <span class="ic">ⓘ</span>Showing images placed in albums. {{ unsortedHidden.toLocaleString() }} more
          {{ unsortedHidden === 1 ? 'is' : 'are' }} unsorted, in their works’ Quick access —
          <button class="linkbtn" @click="f.unsorted = true; reload()">Include them</button>
        </div>
        <div v-if="stale" class="hint">
          <span class="ic">↻</span>The vault changed since this list was read —
          <button class="linkbtn" @click="reload">Refresh</button>
        </div>
      </div>

      <div v-if="!loading && !items.length" class="empty">
        <template v-if="active.length">
          <div class="emoji">⌕</div>
          <p><b>No images match.</b><br />{{ active.map((a) => a.label).join(' · ') }}<br />
            <button class="linkbtn" @click="clearAll">Clear filters</button></p>
        </template>
        <template v-else-if="unsortedHidden">
          <div class="emoji">▦</div>
          <p><b>No images in albums yet.</b><br />Your {{ unsortedHidden.toLocaleString() }} generation{{ unsortedHidden === 1 ? ' is' : 's are' }}
            unsorted, in their works’ Quick access.<br />
            <button class="linkbtn" @click="f.unsorted = true; reload()">Include unsorted images</button></p>
        </template>
        <template v-else>
          <div class="emoji">✦</div>
          <p>No images yet. Generate some in <b>Generate</b> — they show here once they’re in a work.</p>
        </template>
      </div>
      <GalleryGrid v-else :key="listKey" :items="items" :complete="complete" @more="more" @open="open"
        @favorite="toggleFavorite" />
    </template>
  </div>
</template>

<style scoped>
.gallery { flex: 1; display: flex; flex-direction: column; min-width: 0; background: var(--bg); }
.ghead { display: flex; align-items: center; gap: 8px; height: 55px; padding: 0 18px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.ghead h1 { font-size: 18px; font-weight: 700; margin: 0; }
.ghead .count { font-size: 13px; color: var(--text-faint); }
.hsp { flex: 1; }
.sortwrap { position: relative; }
.sortbtn { height: 32px; border: 1px solid var(--border-strong); background: var(--surface-2); color: var(--text-dim);
  border-radius: var(--radius); padding: 0 12px; font: inherit; font-size: 12.5px; font-weight: 600; cursor: pointer; }
.sortbtn:hover { color: var(--text); }
.car { font-size: 10px; opacity: .8; }
.sortmenu { position: absolute; right: 0; top: calc(100% + 5px); z-index: 20; min-width: 160px; padding: 5px;
  border: 1px solid var(--border-strong); border-radius: var(--radius-lg); background: var(--surface-1); box-shadow: 0 10px 30px rgba(0,0,0,.45); }
.srow { display: block; width: 100%; text-align: left; border: 0; background: transparent; color: var(--text);
  font: inherit; font-size: 12.5px; font-weight: 600; padding: 7px 9px; border-radius: var(--radius); cursor: pointer; }
.srow:hover { background: var(--surface-3); }
.srow.on { color: var(--accent); }
.gtools { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 12px 16px 0; flex-shrink: 0; }
.search { position: relative; }
.search input { width: 240px; height: 32px; font: inherit; font-size: 12.5px; color: var(--text); background: var(--surface-2);
  border: 1px solid var(--border); border-radius: var(--radius); padding: 0 10px 0 30px; outline: none; }
.search input:focus { border-color: var(--accent); }
.search .ic { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: var(--text-faint); font-size: 13px; pointer-events: none; }
.tbtn { height: 32px; display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border-strong);
  background: var(--surface-2); color: var(--text-dim); border-radius: var(--radius); padding: 0 11px; font: inherit;
  font-size: 12.5px; font-weight: 600; white-space: nowrap; cursor: pointer; }
.tbtn:hover { color: var(--text); }
.tbtn .st { color: var(--star); }
.tbtn.on { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); background: var(--nav-active); color: var(--accent); }
.sw { height: 32px; display: inline-flex; align-items: center; gap: 8px; border: 1px solid var(--border-strong);
  background: var(--surface-2); color: var(--text-dim); border-radius: var(--radius); padding: 0 11px; font: inherit;
  font-size: 12.5px; font-weight: 600; white-space: nowrap; cursor: pointer; }
.sw:hover { color: var(--text); }
.sw .tr { width: 26px; height: 15px; border-radius: 10px; background: var(--border-strong); position: relative; flex-shrink: 0; transition: background .12s; }
.sw .tr::after { content: ""; position: absolute; left: 2px; top: 2px; width: 11px; height: 11px; border-radius: 50%;
  background: var(--surface-1); transition: left .12s; }
.sw.on { color: var(--text); }
.sw.on .tr { background: var(--accent); }
.sw.on .tr::after { left: 13px; background: var(--on-accent); }
.gtags { margin: 10px 16px 0; }
.ginfo { display: flex; flex-direction: column; gap: 6px; padding: 10px 16px 0; flex-shrink: 0; }
.sum { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 12px; color: var(--text-dim); }
.fchip { display: inline-flex; align-items: center; gap: 4px; border: 1px solid var(--border); background: var(--surface-1);
  border-radius: var(--radius); padding: 1px 3px 1px 8px; font-size: 11.5px; color: var(--text); }
.fchip button { width: 18px; height: 18px; border: 0; border-radius: 4px; background: transparent; color: var(--text-faint);
  font-size: 10px; cursor: pointer; }
.fchip button:hover { background: var(--surface-3); color: var(--text); }
.hint { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; font-size: 12px; color: var(--text-dim); }
.hint .ic { color: var(--text-faint); }
.linkbtn { border: 0; background: transparent; color: var(--accent); cursor: pointer; font: inherit; font-size: 12px; font-weight: 600; padding: 0; }
.gallery :deep(.gscroll) { margin-top: 12px; }
.empty { max-width: 440px; margin: 12vh auto; text-align: center; color: var(--text-dim); }
.empty .emoji { font-size: 34px; color: var(--text-faint); margin-bottom: 12px; }
.empty .emoji.away { color: var(--warn-fg); }
.empty b { color: var(--text); }
.empty p { font-size: 13px; line-height: 1.6; }
.empty .linkbtn { font-size: 13px; }
</style>
