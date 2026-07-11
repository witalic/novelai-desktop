<script setup lang="ts">
/* The library-zone widget (design/prompt-widget-mockup.html, rev 5). Two modes over one zone:
 * Quick access — the work's palette (the zone's child block nodes, packed by CanvasBoard) — and
 * Find in Library — a read-only, race-guarded browser over the vault (usePromptBrowse). Mode and
 * filters are transient by design: a reopened work always starts in Quick access. */
import { computed, onUnmounted, ref, watch } from 'vue'
import { listBlocks, listCategories, listTags } from '../api'
import { partitionPinned, usePromptBrowse } from '../composables/usePromptBrowse'
import type { LibraryBlock, ZoneNode } from '../types'

const props = defineProps<{ data: ZoneNode['data']; selected: boolean; count: number; pinnedIds: string[] }>()
const emit = defineEmits<{
  toggle: []
  'open-library': []
  'open-settings': []
  pin: [LibraryBlock]
  reveal: [string] // a "Pinned ✓" ghost was clicked — flash its palette master (block_id)
  browse: [boolean] // CanvasBoard hides the palette's child nodes while browsing
}>()

const mode = ref<'stash' | 'browse'>('stash')
const {
  search, category, tags, items, total, categories, tagOptions, loading, noVault,
  activate, setSearch, setCategory, toggleTag, clearFilters, loadMore, dispose,
} = usePromptBrowse({ listBlocks, listCategories, listTags })

watch(mode, (m) => {
  emit('browse', m === 'browse')
  if (m === 'browse') activate()
})

// '/' while the canvas has focus → jump into search (CanvasBoard dispatches the event).
const searchEl = ref<HTMLInputElement | null>(null)
function focusSearch() {
  if (props.data.collapsed) return
  mode.value = 'browse'
  requestAnimationFrame(() => searchEl.value?.focus())
}
window.addEventListener('nai:widget-search', focusSearch)

// ---- category dropdown (searchable — chips don't scale past ~20 categories) ----
const catOpen = ref(false)
const catQuery = ref('')
const catColor = (slug: string) => categories.value.find((c) => c.slug === slug)?.color || '#738496'
const catLabel = computed(() => categories.value.find((c) => c.slug === category.value)?.name || 'All categories')
const allCount = computed(() => categories.value.reduce((n, c) => n + c.count, 0))
const catMatches = computed(() => {
  const q = catQuery.value.trim().toLowerCase()
  return categories.value.filter((c) => !q || c.name.toLowerCase().includes(q))
})
function chooseCat(slug: string) {
  catOpen.value = false
  catQuery.value = ''
  if (slug !== category.value) setCategory(slug)
}

// ---- tag filter (typeahead behind "+ filter", AND-semantics) ----
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

// Close the dropdowns on any pointer-down outside the widget.
const rootEl = ref<HTMLElement | null>(null)
function onDocPointer(e: MouseEvent) {
  if (rootEl.value && !rootEl.value.contains(e.target as Node)) { catOpen.value = false; tagOpen.value = false }
}
document.addEventListener('mousedown', onDocPointer)
onUnmounted(() => {
  window.removeEventListener('nai:widget-search', focusSearch)
  document.removeEventListener('mousedown', onDocPointer)
  dispose()
})

// Pin → CanvasBoard materialises a palette node; the row shows its ✓ for a beat, THEN leaves the
// pool (a just-pinned id is exempt from exclusion until the flash ends — otherwise the row would
// vanish under the cursor with no feedback).
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

// Exclusive membership: pinned blocks leave the pool; an active search shows them as ghosts.
const pinnedSet = computed(() => {
  const s = new Set(props.pinnedIds)
  for (const id of Object.keys(justPinned.value)) s.delete(id)
  return s
})
const parts = computed(() => partitionPinned(items.value, pinnedSet.value, !!search.value.trim()))
// The backend total counts the whole vault — subtract the pinned rows found in loaded pages so
// the footer reads as "the pool" (exact once every page is loaded).
const poolTotal = computed(() => Math.max(parts.value.visible.length, total.value - (items.value.length - parts.value.visible.length)))

function revealPinned(b: LibraryBlock) {
  mode.value = 'stash'
  emit('reveal', b.id)
}

function onListScroll(e: Event) {
  const el = e.target as HTMLElement
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 80) loadMore()
}
</script>

<template>
  <div ref="rootEl" class="pwidget" :class="{ selected, collapsed: data.collapsed }">
    <div class="pwhd">
      <span class="picon">✦</span>
      <span class="ptitle">Prompt blocks</span>
      <span class="anchor-tag">anchor</span>
      <button class="pcollapse nodrag" :title="data.collapsed ? 'Expand' : 'Collapse to header'"
        @pointerdown.stop @click.stop="$emit('toggle')">{{ data.collapsed ? '▸' : '▾' }}</button>
    </div>

    <template v-if="!data.collapsed">
      <div class="pwmode nodrag" @pointerdown.stop>
        <button :class="{ active: mode === 'stash' }" @click="mode = 'stash'">Quick access <span class="n">{{ count }}</span></button>
        <button :class="{ active: mode === 'browse' }" @click="mode = 'browse'">Find in Library</button>
      </div>

      <!-- Quick access: the palette itself is the zone's child nodes; only chrome renders here. -->
      <template v-if="mode === 'stash'">
        <div class="pwbody">
          <div v-if="!count" class="pwhint">
            Nothing pinned yet.<br />
            <button class="plink nodrag" @pointerdown.stop @click.stop="mode = 'browse'">Find in Library</button>
          </div>
        </div>
        <div class="pwfoot">
          <span>{{ count }} pinned</span>
          <a class="plib nodrag" @pointerdown.stop @click.stop="mode = 'browse'">Find in Library ⌕</a>
        </div>
      </template>

      <!-- Find in Library: read-only vault browser. -->
      <template v-else>
        <div class="pwtools nodrag" @pointerdown.stop>
          <div class="pwsearch">
            <span class="ic">⌕</span>
            <input ref="searchEl" type="text" placeholder="Search blocks…  ( / )" :value="search"
              @input="setSearch(($event.target as HTMLInputElement).value)" />
          </div>
          <div class="catdd">
            <button class="catbtn" @click="catOpen = !catOpen">
              <span v-if="category" class="cdot" :style="{ background: catColor(category) }"></span>
              <span class="lbl">{{ catLabel }}</span>
              <span class="car">▾</span>
            </button>
            <div v-if="catOpen" class="catpanel nowheel">
              <input v-if="categories.length > 8" v-model="catQuery" type="text" placeholder="Filter categories…" />
              <button class="catitem" :class="{ on: !category }" @click="chooseCat('')">
                All categories <span class="n">{{ allCount }}</span>
              </button>
              <button v-for="c in catMatches" :key="c.slug" class="catitem" :class="{ on: c.slug === category }"
                @click="chooseCat(c.slug)">
                <span class="cdot" :style="{ background: c.color }"></span>{{ c.name }} <span class="n">{{ c.count }}</span>
              </button>
            </div>
          </div>
          <div class="tagrow">
            <span class="tglbl">Tags</span>
            <span v-for="t in tags" :key="t" class="ftag">{{ t }} <button title="Remove" @click="toggleTag(t)">✕</button></span>
            <span class="tagadd">
              <button v-if="!tagOpen" class="addtag" @click="tagOpen = true">+ filter</button>
              <input v-else v-model="tagQuery" type="text" placeholder="tag…"
                @keyup.esc="tagOpen = false" />
              <div v-if="tagOpen && tagMatches.length" class="tagpanel nowheel">
                <button v-for="t in tagMatches" :key="t.name" @click="addTag(t.name)">{{ t.name }} <span class="n">{{ t.count }}</span></button>
              </div>
            </span>
          </div>
        </div>

        <div class="pwlist nodrag nowheel" @pointerdown.stop @scroll="onListScroll">
          <template v-if="noVault">
            <div class="pwhint">Connect a vault to browse your prompt blocks.<br />
              <button class="plink" @click="$emit('open-settings')">Open Settings</button>
            </div>
          </template>
          <template v-else-if="loading && !items.length">
            <div v-for="i in 3" :key="i" class="skel"></div>
          </template>
          <template v-else-if="!parts.visible.length && !parts.ghosts.length">
            <div class="pwhint">No blocks match.<br />
              <button class="plink" @click="clearFilters()">Clear filters</button>
            </div>
          </template>
          <template v-else>
            <div v-for="b in parts.visible" :key="b.id" class="brow" :style="{ '--cat': catColor(b.category) }">
              <div class="r1">
                <span class="bname">{{ b.name }}</span>
                <span class="pol" :class="{ neg: b.polarity === 'negative' }">{{ b.polarity === 'negative' ? '−' : '＋' }}</span>
              </div>
              <div class="btext">{{ b.text }}</div>
              <button class="act" :class="{ done: justPinned[b.id] }" title="Pin into quick access"
                @click="pin(b)">{{ justPinned[b.id] ? '✓' : 'Pin' }}</button>
            </div>
            <!-- search never lies: pinned matches show greyed-out; click = jump to the palette master -->
            <div v-for="b in parts.ghosts" :key="`pinned-${b.id}`" class="brow pinned"
              :style="{ '--cat': catColor(b.category) }" title="Already in quick access — click to show it"
              @click="revealPinned(b)">
              <div class="r1">
                <span class="bname">{{ b.name }}</span>
                <span class="pol" :class="{ neg: b.polarity === 'negative' }">{{ b.polarity === 'negative' ? '−' : '＋' }}</span>
              </div>
              <div class="btext">{{ b.text }}</div>
              <span class="pinchip">Pinned ✓</span>
            </div>
          </template>
        </div>
        <div class="pwfoot">
          <span>{{ parts.visible.length }} of {{ poolTotal }} blocks</span>
          <a class="plib nodrag" @pointerdown.stop @click.stop="$emit('open-library')">Open Library ↗</a>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.pwidget{width:100%;height:100%;display:flex;flex-direction:column;border:1.5px solid var(--border-strong);
  border-radius:12px;overflow:hidden;background:color-mix(in srgb,var(--surface-1) 60%,transparent)}
.pwidget.selected{border-color:var(--accent)}
.pwhd{display:flex;align-items:center;gap:8px;height:38px;flex-shrink:0;padding:0 12px;
  border-bottom:1px solid var(--border);background:var(--surface-1);font-weight:600;font-size:13px}
.pwidget.collapsed .pwhd{border-bottom:0}
.picon{font-style:normal}
.ptitle{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.anchor-tag{margin-left:auto;font-size:10px;font-weight:600;color:var(--accent);
  background:color-mix(in srgb,var(--accent) 16%,transparent);padding:1px 7px;border-radius:20px}
.pcollapse{border:0;background:transparent;color:var(--text-faint);cursor:pointer;font-size:11px;padding:2px 4px}
.pcollapse:hover{color:var(--text)}

.pwmode{display:flex;flex-shrink:0;margin:8px 10px 0;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.pwmode button{flex:1;border:0;border-left:1px solid var(--border);background:var(--surface-1);color:var(--text-dim);
  font:inherit;font-size:11.5px;font-weight:600;padding:6px 4px;cursor:pointer}
.pwmode button:first-child{border-left:0}
.pwmode button.active{background:var(--nav-active);color:var(--accent)}
.pwmode .n{color:var(--text-faint);font-weight:500;font-variant-numeric:tabular-nums}
.pwmode button.active .n{color:var(--accent)}

.pwbody{flex:1;min-height:0}
.pwhint{padding:16px;font-size:12px;color:var(--text-faint);text-align:center}
.plink{border:0;background:transparent;color:var(--accent);cursor:pointer;font-size:12px;padding:2px 0}

.pwtools{display:flex;flex-direction:column;gap:8px;padding:10px 10px 8px;border-bottom:1px solid var(--border);flex-shrink:0}
.pwsearch{position:relative}
.pwsearch input{width:100%;font:inherit;font-size:12px;color:var(--text);background:var(--surface-2);
  border:1px solid var(--border);border-radius:var(--radius);padding:6px 8px 6px 26px;outline:none}
.pwsearch input:focus{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 30%,transparent)}
.pwsearch .ic{position:absolute;left:8px;top:50%;transform:translateY(-50%);color:var(--text-faint);font-size:12px;pointer-events:none}

.catdd{position:relative}
.catbtn{width:100%;display:flex;align-items:center;gap:6px;border:1px solid var(--border);border-radius:var(--radius);
  background:var(--surface-2);color:var(--text);font:inherit;font-size:11.5px;font-weight:600;padding:5px 8px;cursor:pointer}
.catbtn .lbl{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.catbtn .car{margin-left:auto;color:var(--text-faint);font-size:10px}
.catbtn:hover{border-color:var(--border-strong)}
.cdot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
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

.pwlist{flex:1;min-height:0;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:6px}
.brow{position:relative;flex-shrink:0;border:1px solid var(--border);border-left:3px solid var(--cat,#738496);
  border-radius:8px;background:var(--surface-2);padding:6px 8px 7px}
.brow:hover{border-color:var(--border-strong);border-left-color:var(--cat,#738496)}
.brow .r1{display:flex;align-items:center;gap:6px}
.brow .bname{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.brow .pol{margin-left:auto;flex-shrink:0;font-size:11px;font-weight:700;color:var(--text-faint)}
.brow .pol.neg{color:var(--danger,#e2483d)}
.brow .btext{font-size:11px;color:var(--text-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.brow .act{position:absolute;right:6px;bottom:6px;opacity:0;border:1px solid var(--border-strong);
  background:var(--surface-1);color:var(--text-dim);border-radius:5px;height:20px;min-width:20px;font-size:10.5px;
  font-weight:600;line-height:1;cursor:pointer;transition:opacity .1s;padding:0 6px}
.brow:hover .act{opacity:1}
.brow .act:hover{color:var(--accent);border-color:var(--accent)}
.brow .act.done{color:var(--ok,#3aa675);border-color:var(--ok,#3aa675);opacity:1}
.brow.pinned{opacity:.55;cursor:pointer}
.brow.pinned:hover{opacity:.8}
.pinchip{position:absolute;right:8px;bottom:7px;font-size:10px;font-weight:700;color:var(--ok,#3aa675)}
.skel{flex-shrink:0;height:40px;border-radius:8px;background:linear-gradient(90deg,var(--surface-2),var(--surface-3),var(--surface-2));
  background-size:200% 100%;animation:sh 1.4s ease infinite}
@keyframes sh{to{background-position:-200% 0}}
@media (prefers-reduced-motion: reduce){.skel{animation:none;background:var(--surface-2)}}

.pwfoot{flex-shrink:0;display:flex;align-items:center;gap:8px;border-top:1px solid var(--border);
  background:var(--surface-1);padding:6px 12px;font-size:11px;color:var(--text-faint)}
.plib{margin-left:auto;color:var(--accent);cursor:pointer;font-weight:600;text-decoration:none}
</style>
