<script setup lang="ts">
/* The library-zone widget (design/prompt-widget-rework-mockup.html, rev 3). One direct browser over
 * the whole vault Library, laid out like the full Library view: a category rail on the left (with a
 * ★ Favorites toggle on top), a search + per-category tag pins in the header, and the blocks below.
 * There is no palette — every block that enters the work is an independent copy dropped into the
 * station (⇢ / drag) or spawned there (＋ New block). The only per-work state is `favorites` (Library
 * block ids), owned by CanvasBoard; the widget reads it as a prop and toggles it via an emit. The
 * zone drags by its header only (dragHandle); interactive areas stop node select/drag. */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { listBlocks, listCategories, listTags, resolveBlocks } from '../api'
import { usePromptLibrary } from '../composables/usePromptLibrary'
import type { LibraryBlock, ZoneNode } from '../types'

const props = defineProps<{
  data: ZoneNode['data']
  selected: boolean
  favorites: string[]
  revalidate: number // bumped by CanvasBoard when the Library may have changed (re-read categories)
  usedKeys?: Set<string> // polarity+text identities already in the generation composition → grey the row
}>()
const emit = defineEmits<{
  'open-library': [{ category: string; tags: string[] }] // carry the category + tag filter (favorites ignored)
  'open-settings': []
  use: [LibraryBlock] // ⇢ — an independent copy into the station lane (strict polarity routing)
  'new-block': [string] // ＋ New block in the current category ('' → custom)
  'toggle-favorite': [string] // ★ — star/unstar a Library block for this work
}>()

const {
  search, category, tags, favOnly, items, total, allCount, categories, tagOptions, loading, noVault,
  activate, reload, setSearch, setCategory, toggleTag, setFavOnly, clearFilters, loadMore, onFavoritesChanged, dispose,
} = usePromptLibrary({ listBlocks, listCategories, listTags, resolveBlocks, favorites: () => props.favorites })

onMounted(activate)
watch(() => props.revalidate, reload)
// Favorites changed (star toggled / work reopened) → refresh the fav view + rail counts.
watch(() => props.favorites, onFavoritesChanged, { deep: true })

const favSet = computed(() => new Set(props.favorites))
// A block already in the generation composition (same polarity + text) — greyed to signal "already added".
const blockKey = (polarity: string, text: string) => `${polarity === 'negative' ? 'neg' : 'pos'}:${String(text || '').trim().toLowerCase()}`
const isUsed = (b: LibraryBlock) => props.usedKeys?.has(blockKey(b.polarity, b.text)) ?? false
const catColor = (slug: string) => categories.value.find((c) => c.slug === slug)?.color || '#738496'
const catName = (slug: string) => categories.value.find((c) => c.slug === slug)?.name
  || (slug.startsWith('cat-') ? 'custom' : slug)
const newLabel = computed(() => (category.value ? catName(category.value) : 'custom'))
const tagLabel = computed(() => (category.value ? `Tags · ${catName(category.value)}` : 'Tags · all') + (favOnly.value ? ' · ★' : ''))
const filterSummary = computed(() => {
  const parts: string[] = []
  if (category.value) parts.push(catName(category.value))
  if (favOnly.value) parts.push('★ favorites')
  if (tags.value.length) parts.push(`${tags.value.length} tag${tags.value.length > 1 ? 's' : ''}`)
  return parts.join(' · ')
})

// Presses on interactive elements must not select/drag the zone; empty widget areas still behave as
// the node. (Vue Flow selects on `click`, drags from `pointerdown` — one guard covers all three.)
function stopIfInteractive(e: Event) {
  const t = e.target as HTMLElement | null
  if (t && t !== e.currentTarget && t.closest('button, input, textarea, a, .brow')) e.stopPropagation()
}

// '/' while the canvas has focus → jump into the search (CanvasBoard dispatches the event).
const searchEl = ref<HTMLInputElement | null>(null)
function focusSearch() {
  requestAnimationFrame(() => searchEl.value?.focus())
}
window.addEventListener('nai:widget-search', focusSearch)

// Hover details card: shown after a short dwell, teleported beside the widget (the zone clips overflow).
const rootEl = ref<HTMLElement | null>(null)
const hover = ref<{ block: LibraryBlock; x: number; y: number } | null>(null)
let hoverTimer: ReturnType<typeof setTimeout> | null = null
function onRowEnter(e: MouseEvent, b: LibraryBlock) {
  if (hoverTimer) clearTimeout(hoverTimer)
  const row = e.currentTarget as HTMLElement
  hoverTimer = setTimeout(() => {
    const r = row.getBoundingClientRect()
    const wr = rootEl.value?.getBoundingClientRect()
    hover.value = { block: b, x: (wr ? wr.right : r.right) + 8, y: Math.min(r.top, window.innerHeight - 260) }
  }, 250)
}
function onRowLeave() {
  if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null }
  hover.value = null
}

onUnmounted(() => {
  window.removeEventListener('nai:widget-search', focusSearch)
  if (hoverTimer) clearTimeout(hoverTimer)
  dispose()
})

function onRowDragStart(e: DragEvent, b: LibraryBlock) {
  if (!e.dataTransfer) return
  e.dataTransfer.effectAllowed = 'copy'
  e.dataTransfer.setData('text/plain', `nai-libblock:${JSON.stringify(b)}`)
}
function onListScroll(e: Event) {
  onRowLeave() // don't leave a card stranded over a scrolled-away row
  const el = e.target as HTMLElement
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 80) loadMore()
}
</script>

<template>
  <div ref="rootEl" class="pwidget" :class="{ selected }">
    <div class="pwhd">
      <span class="picon">✦</span>
      <span class="ptitle">Prompt blocks</span>
    </div>

    <div class="pwbody nodrag" @pointerdown="stopIfInteractive" @mousedown="stopIfInteractive" @click="stopIfInteractive">
      <!-- left rail: ★ Favorites toggle + All + categories (single-select) -->
      <div class="pwrail">
        <div class="railscroll nowheel">
          <button class="catnav fav" :class="{ on: favOnly }" title="Filter to this work’s favorites — combines with the category"
            @click="setFavOnly(!favOnly)">
            <span class="gl">{{ favOnly ? '★' : '☆' }}</span><span class="cn">Favorites</span><span class="cc">{{ favorites.length }}</span>
          </button>
          <div class="railsep"></div>
          <button class="catnav" :class="{ on: !category }" @click="setCategory('')">
            <span class="gl">▦</span><span class="cn">All blocks</span><span class="cc">{{ allCount }}</span>
          </button>
          <div class="railgroup">Categories</div>
          <button v-for="c in categories" :key="c.slug" class="catnav" :class="{ on: category === c.slug }" @click="setCategory(c.slug)">
            <span class="cdot" :style="{ background: c.color }"></span><span class="cn">{{ c.name }}</span><span class="cc">{{ c.count }}</span>
          </button>
        </div>
      </div>

      <!-- content: header (search + tags) → list -->
      <div class="pwcontent">
        <div class="pwhead">
          <div class="pwsearch">
            <span class="ic">⌕</span>
            <input ref="searchEl" type="text" placeholder="Search name, prompt, tags…  ( / )" :value="search"
              @input="setSearch(($event.target as HTMLInputElement).value)" />
          </div>
          <div class="tagsec">
            <span class="tl">{{ tagLabel }}</span>
            <div class="tagwrap nowheel">
              <button v-for="t in tagOptions" :key="t.name" class="tchip" :class="{ on: tags.includes(t.name) }" @click="toggleTag(t.name)">
                {{ t.name }} <span class="n">{{ t.count }}</span>
              </button>
              <span v-if="!tagOptions.length" class="tagempty">No tags in this view.</span>
            </div>
          </div>
        </div>

        <div class="pwlist nowheel" @scroll="onListScroll">
          <template v-if="noVault">
            <div class="wempty">Connect a vault to browse your prompt blocks.<br />
              <button @click="$emit('open-settings')">Open Settings</button>
            </div>
          </template>
          <template v-else>
            <button class="newrow" @click="$emit('new-block', category)">＋ New block in <b>{{ newLabel }}</b></button>
            <template v-if="loading && !items.length">
              <div v-for="i in 3" :key="i" class="skel"></div>
            </template>
            <template v-else-if="favOnly && !items.length">
              <div class="wempty">No favorites{{ category ? ` in ${catName(category)}` : '' }} yet.<br />
                Star blocks to build your quick-access set.<br />
                <button @click="setFavOnly(false)">Turn off favorites</button>
              </div>
            </template>
            <template v-else-if="!items.length">
              <div class="wempty">No blocks match.<br /><button @click="clearFilters">Clear filters</button></div>
            </template>
            <template v-else>
              <div v-for="b in items" :key="b.id" class="brow" :class="[b.polarity === 'negative' ? 'neg' : 'pos', { used: isUsed(b) }]"
                :title="isUsed(b) ? 'Already in the generation area' : undefined"
                draggable="true" @dragstart="onRowDragStart($event, b)"
                @mouseenter="onRowEnter($event, b)" @mouseleave="onRowLeave">
                <div class="r1">
                  <span class="grip">⠿</span>
                  <span class="catdot" :style="{ background: catColor(b.category) }" :title="catName(b.category)"></span>
                  <span class="bname">{{ b.name }}</span>
                  <span v-if="b.polarity === 'negative'" class="negtag">NEG</span>
                  <span class="spacer"></span>
                  <button class="ricon fav" :class="{ on: favSet.has(b.id) }"
                    :title="favSet.has(b.id) ? 'Remove from favorites' : 'Add to this work’s favorites'"
                    @click="$emit('toggle-favorite', b.id)">{{ favSet.has(b.id) ? '★' : '☆' }}</button>
                  <button class="ricon move" :title="`Move a copy into the ${b.polarity === 'negative' ? 'negative' : 'positive'} lane`"
                    @click="$emit('use', b)">⇢</button>
                </div>
                <div class="btext">{{ b.text }}</div>
              </div>
            </template>
          </template>
        </div>
      </div>
    </div>

    <div class="pwfoot nodrag" @pointerdown="stopIfInteractive" @mousedown="stopIfInteractive" @click="stopIfInteractive">
      <span>{{ items.length }} block{{ items.length === 1 ? '' : 's' }}{{ filterSummary ? ` · ${filterSummary}` : '' }}<template v-if="!favOnly && total > items.length"> of {{ total }}</template></span>
      <a class="plib nodrag" @click.stop="$emit('open-library', { category, tags: [...tags] })">Open in Library ↗</a>
    </div>

    <!-- hover details card — teleported to body (the zone node clips overflow), positioned beside the row -->
    <Teleport to="body">
      <div v-if="hover" class="pwpop" :style="{ left: hover.x + 'px', top: hover.y + 'px' }">
        <div class="pph">
          <span class="cdot" :style="{ background: catColor(hover.block.category) }"></span>
          <span class="ppname">{{ hover.block.name }}</span>
          <span v-if="hover.block.polarity === 'negative'" class="negtag">NEG</span>
        </div>
        <div class="ppmeta">{{ catName(hover.block.category) }} · {{ hover.block.polarity }} block</div>
        <div class="pplbl">Prompt</div>
        <div class="pptext">{{ hover.block.text }}</div>
        <template v-if="hover.block.tags.length">
          <div class="pplbl">Gallery tags</div>
          <div class="pptags"><span v-for="t in hover.block.tags" :key="t">{{ t }}</span></div>
        </template>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.pwidget{position:relative;width:100%;height:100%;display:flex;flex-direction:column;border:1.5px solid var(--border-strong);
  border-radius:12px;overflow:hidden;background:color-mix(in srgb,var(--surface-1) 92%,transparent)}
.pwidget.selected{border-color:var(--accent)}
.pwhd{display:flex;align-items:center;gap:8px;height:38px;flex-shrink:0;padding:0 8px 0 12px;
  border-bottom:1px solid var(--border);background:var(--surface-1);font-weight:600;font-size:13px;cursor:grab}
.picon{font-style:normal}
.ptitle{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

.pwbody{display:flex;flex:1;min-height:0}
.pwrail{width:138px;flex-shrink:0;border-right:1px solid var(--border);display:flex;flex-direction:column;
  background:color-mix(in srgb,var(--surface-1) 60%,transparent)}
.railscroll{flex:1;min-height:0;overflow-y:auto;padding:6px 6px 8px}
.railgroup{font-size:9.5px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint);padding:8px 8px 4px}
.catnav{display:flex;align-items:center;gap:7px;width:100%;border:0;background:transparent;color:var(--text-dim);
  font:inherit;font-size:11.5px;font-weight:600;padding:6px 7px;border-radius:var(--radius);cursor:pointer;text-align:left}
.catnav:hover{background:var(--surface-3);color:var(--text)}
.catnav.on{background:var(--nav-active);color:var(--accent)}
.catnav .cdot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.catnav .gl{width:8px;text-align:center;flex-shrink:0;font-size:11px;line-height:1}
.catnav .cn{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.catnav .cc{margin-left:auto;color:var(--text-faint);font-weight:500;font-variant-numeric:tabular-nums;font-size:10.5px}
.catnav.on .cc{color:var(--accent)}
.catnav.fav .gl{color:var(--star,#e0a534)}
.catnav.fav.on{background:color-mix(in srgb,var(--star,#e0a534) 15%,transparent);color:var(--star,#e0a534)}
.catnav.fav.on .cc{color:var(--star,#e0a534)}
.railsep{height:1px;background:var(--border);margin:6px 8px 2px}

/* one shared horizontal gutter so the tag-scroll and the list-scroll line up on the same inset */
.pwcontent{display:flex;flex-direction:column;flex:1;min-width:0;padding:0 8px}
.pwhead{display:flex;flex-direction:column;gap:8px;padding:9px 0;border-bottom:1px solid var(--border);flex-shrink:0}
.pwsearch{position:relative}
.pwsearch input{width:100%;font:inherit;font-size:12px;color:var(--text);background:var(--surface-2);
  border:1px solid var(--border);border-radius:var(--radius);padding:7px 9px 7px 28px;outline:none}
.pwsearch input:focus{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 30%,transparent)}
.pwsearch .ic{position:absolute;left:9px;top:50%;transform:translateY(-50%);color:var(--text-faint);font-size:12px;pointer-events:none}
.tagsec{display:flex;flex-direction:column;gap:5px}
.tl{font-size:9.5px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint)}
.tagwrap{display:flex;flex-wrap:wrap;gap:5px;max-height:64px;overflow-y:auto}
.tchip{flex-shrink:0;display:inline-flex;align-items:center;gap:5px;border:1px solid var(--border);
  background:var(--surface-2);color:var(--text-dim);border-radius:20px;padding:2px 9px;font-size:11px;
  font-weight:600;cursor:pointer;white-space:nowrap}
.tchip .n{color:var(--text-faint);font-weight:500;font-variant-numeric:tabular-nums;font-size:10px}
.tchip:hover{border-color:var(--border-strong);color:var(--text)}
.tchip.on{background:var(--nav-active);border-color:color-mix(in srgb,var(--accent) 45%,var(--border));color:var(--accent)}
.tchip.on .n{color:var(--accent)}
.tagempty{font-size:11px;color:var(--text-faint);padding:2px}

.pwlist{flex:1;min-height:0;overflow-y:auto;padding:8px 0;display:flex;flex-direction:column;gap:6px}
.newrow{flex-shrink:0;display:flex;align-items:center;justify-content:center;gap:6px;width:100%;
  border:1px dashed var(--border-strong);border-radius:8px;background:transparent;color:var(--text-dim);
  font:inherit;font-size:11.5px;font-weight:600;padding:8px;cursor:pointer}
.newrow:hover{color:var(--accent);border-color:var(--accent);background:color-mix(in srgb,var(--accent) 6%,transparent)}
.newrow b{color:var(--text)}

/* left border = polarity (green positive / red negative); category = a dot before the name */
.brow{position:relative;flex-shrink:0;border:1px solid var(--border);border-left:4px solid var(--pol);
  border-radius:8px;background:var(--surface-2);padding:6px 8px 7px;cursor:grab;transition:border-color .1s}
.brow.pos{--pol:var(--ok,#3aa675)}
.brow.neg{--pol:var(--danger,#e2483d);background:color-mix(in srgb,var(--danger,#e2483d) 7%,var(--surface-2))}
.brow.used{--pol:var(--border-strong);background:color-mix(in srgb,var(--surface-3) 55%,transparent);opacity:.6} /* already in the generation area */
.brow:hover{border-color:var(--border-strong);border-left-color:var(--pol)}
.brow .r1{display:flex;align-items:center;gap:6px;min-height:22px}
.grip{color:var(--text-faint);font-size:10px;cursor:grab;flex-shrink:0}
.catdot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.bname{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.negtag{flex-shrink:0;font-size:8.5px;font-weight:800;letter-spacing:.4px;color:var(--danger,#e2483d);
  border:1px solid color-mix(in srgb,var(--danger,#e2483d) 50%,var(--border));background:color-mix(in srgb,var(--danger,#e2483d) 12%,transparent);
  border-radius:9px;padding:0 6px;line-height:15px}
.spacer{margin-left:auto}
.ricon{flex-shrink:0;border:1px solid var(--border-strong);background:var(--surface-1);color:var(--text-dim);
  border-radius:5px;height:22px;min-width:22px;font-size:12px;font-weight:600;line-height:1;cursor:pointer;padding:0 5px;
  display:inline-flex;align-items:center;justify-content:center}
.ricon:hover{color:var(--accent);border-color:var(--accent)}
.fav{color:var(--text-faint)}
.fav.on{color:var(--star,#e0a534);border-color:color-mix(in srgb,var(--star,#e0a534) 55%,var(--border));
  background:color-mix(in srgb,var(--star,#e0a534) 12%,transparent)}
.fav:hover{color:var(--star,#e0a534);border-color:var(--star,#e0a534)}
.btext{font-size:11px;color:var(--text-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}

.wempty{padding:22px 14px;text-align:center;font-size:12px;color:var(--text-faint)}
.wempty button{border:0;background:transparent;color:var(--accent);cursor:pointer;font-size:12px;padding:0}
.skel{flex-shrink:0;height:42px;border-radius:8px;background:linear-gradient(90deg,var(--surface-2),var(--surface-3),var(--surface-2));
  background-size:200% 100%;animation:sh 1.4s ease infinite}
@keyframes sh{to{background-position:-200% 0}}
@media (prefers-reduced-motion: reduce){.skel{animation:none;background:var(--surface-2)}}

.pwfoot{flex-shrink:0;display:flex;align-items:center;gap:8px;border-top:1px solid var(--border);
  background:var(--surface-1);padding:7px 12px;font-size:11px;color:var(--text-faint)}
.plib{margin-left:auto;color:var(--accent);cursor:pointer;font-weight:600;text-decoration:none;white-space:nowrap}

/* hover details card (teleported to body — keeps this component's scoped styles) */
.pwpop{position:fixed;z-index:2200;width:288px;border:1px solid var(--border-strong);border-radius:8px;
  background:var(--surface-1);box-shadow:0 8px 30px rgba(0,0,0,.4);padding:12px 14px;pointer-events:none}
.pwpop .pph{display:flex;align-items:center;gap:7px;margin-bottom:6px}
.pwpop .cdot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.pwpop .ppname{font-size:12.5px;font-weight:700}
.pwpop .negtag{margin-left:auto}
.pwpop .ppmeta{font-size:10.5px;color:var(--text-faint);margin-bottom:8px;text-transform:capitalize}
.pwpop .pplbl{font-size:9.5px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:var(--text-faint);margin:0 0 3px}
.pwpop .pptext{font-size:12px;color:var(--text);background:var(--surface-2);border:1px solid var(--border);border-radius:6px;
  padding:7px 9px;margin:0 0 8px;line-height:1.5;white-space:pre-wrap;word-break:break-word}
.pwpop .pptags{display:flex;flex-wrap:wrap;gap:4px}
.pwpop .pptags span{font-size:10.5px;background:var(--surface-3);border:1px solid var(--border);border-radius:20px;padding:1px 8px;color:var(--text-dim)}
</style>
