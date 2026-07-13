<script setup lang="ts">
// Structured gallery — a canvas node (in the `gallery` zone) that renders the work's gallery-role
// images as a composable stack of typed blocks (design/gallery-widget-mockup.html). Increment 1: a
// single image-grid block over the passed gallery images, with a column control, ★ favourite, and
// click-to-preview. More block types (Section/Heading/Text/Metadata/Divider) land in later increments.
import { computed, ref } from 'vue'
import { filterBySource } from './gallerySource'
import type { GalleryBlock, ImageNodeData, ZoneNode } from '../types'

// Minimal shape the widget reads off a gallery image node (avoids coupling to Vue Flow's node type).
interface GalleryImage { id: string; data: Partial<ImageNodeData> }

const props = defineProps<{
  data: ZoneNode['data'] // the gallery zone's reactive data (holds `blocks`) — mutated in place, Vue-Flow-tracked
  images: GalleryImage[] // the work's gallery-role image nodes, ordered oldest→newest
  title?: string
}>()
const emit = defineEmits<{ favorite: [string]; preview: [string] }>()

const blocks = computed<GalleryBlock[]>(() => props.data.blocks ?? [])
const total = computed(() => props.images.length)

// A grid's images come from a saved query (all / favorites / tag:x / group:g), never a stored id list.
const sourceImages = (source: string): GalleryImage[] => filterBySource(props.images, source)

// ---- source picker (which images a grid shows) ----
// The distinct tags/groups present across the work's gallery images, offered as `tag:`/`group:` sources.
const availableSources = computed(() => {
  const tags = new Set<string>(), groups = new Set<string>()
  for (const im of props.images) {
    for (const t of im.data.tags || []) tags.add(t)
    if (im.data.group) groups.add(im.data.group)
  }
  return { tags: [...tags].sort(), groups: [...groups].sort() }
})
function sourceLabel(s: string): string {
  if (s === 'all') return 'All images'
  if (s === 'favorites') return 'Favourites'
  if (s.startsWith('tag:')) return `Tag · ${s.slice(4)}`
  if (s.startsWith('group:')) return `Group · ${s.slice(6)}`
  return s
}
const sourceOpen = ref<string | null>(null) // block id whose picker is open — transient, kept off the block object
function toggleSource(id: string) { sourceOpen.value = sourceOpen.value === id ? null : id }
function pickSource(b: GalleryBlock, s: string) { if (b.type === 'grid') b.source = s; sourceOpen.value = null } // in-place → persists

const dpr = Math.min(typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1, 2)
// Server-sized thumbnail for a cell; a fresh `data:` URL can't be resized so it's used as-is.
function thumbSrc(url: string | undefined, cols: number): string {
  if (!url || url.startsWith('data:')) return url || ''
  const cellPx = Math.round(700 / cols) // node ≈ 700px wide inside padding
  return `${url}?w=${Math.round(cellPx * dpr * 1.4)}`
}

function setCols(b: GalleryBlock, cols: 2 | 3 | 4) { if (b.type === 'grid') b.cols = cols } // in-place → tracked + autosaved
</script>

<template>
  <div class="gnode" :class="{ selected: false }">
    <div class="gnhd">
      <span class="ic">▦</span>
      <span class="ttl">Gallery</span>
      <span v-if="title" class="ctx">· {{ title }}</span>
      <span class="ctx">· {{ total }} image{{ total === 1 ? '' : 's' }}</span>
    </div>

    <div class="gnbody nowheel" @scroll="sourceOpen = null">
      <div class="glist">
        <template v-for="b in blocks" :key="b.id">
          <!-- image grid -->
          <div v-if="b.type === 'grid'" class="b-grid">
            <div class="gridtool nodrag">
              <button class="gtsource" title="Which images this grid shows" @pointerdown.stop @click.stop="toggleSource(b.id)">
                <span class="k">Source:</span> {{ sourceLabel(b.source) }} <span class="car">▾</span>
              </button>
              <div class="cols">
                <button v-for="n in ([2, 3, 4] as const)" :key="n" class="nodrag" :class="{ on: b.cols === n }"
                  @pointerdown.stop @click.stop="setCols(b, n)">{{ n }}</button>
              </div>
              <span class="gtcount">{{ sourceImages(b.source).length }} images</span>
            </div>
            <!-- inline source menu (inline, not fixed/absolute — a Vue-Flow node's transform breaks fixed and its scroll clips absolute) -->
            <div v-if="sourceOpen === b.id" class="srcmenu nodrag" @pointerdown.stop @click.stop>
              <button class="so" :class="{ on: b.source === 'all' }" @click="pickSource(b, 'all')">All images</button>
              <button class="so" :class="{ on: b.source === 'favorites' }" @click="pickSource(b, 'favorites')">★ Favourites</button>
              <template v-if="availableSources.tags.length">
                <div class="sohd">Tags</div>
                <button v-for="t in availableSources.tags" :key="'t' + t" class="so" :class="{ on: b.source === 'tag:' + t }" @click="pickSource(b, 'tag:' + t)">{{ t }}</button>
              </template>
              <template v-if="availableSources.groups.length">
                <div class="sohd">Groups</div>
                <button v-for="g in availableSources.groups" :key="'g' + g" class="so" :class="{ on: b.source === 'group:' + g }" @click="pickSource(b, 'group:' + g)">{{ g }}</button>
              </template>
            </div>
            <div v-if="sourceImages(b.source).length" class="gimgs" :style="{ '--cols': b.cols }">
              <div v-for="im in sourceImages(b.source)" :key="im.id" class="gthumb nodrag" :class="{ fav: im.data.favorite }"
                :style="{ '--ar': im.data.ar || (3 / 4) }" @pointerdown.stop @click.stop="emit('preview', im.data.url || '')">
                <img class="im" :src="thumbSrc(im.data.url, b.cols)" alt="gallery image" loading="lazy" draggable="false" />
                <button class="star nodrag" title="Toggle favourite" @pointerdown.stop @click.stop="emit('favorite', im.id)">★</button>
              </div>
            </div>
            <div v-else class="ghint">
              {{ total ? 'No images match this source.' : 'Generate images and drag them into the Gallery to keep them here.' }}
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.gnode{width:100%;height:100%;display:flex;flex-direction:column;overflow:hidden;border-radius:11px;
  background:color-mix(in srgb,var(--surface-1) 94%,transparent)}
.gnhd{display:flex;align-items:center;gap:8px;height:40px;flex-shrink:0;padding:0 12px;border-bottom:1px solid var(--border);background:var(--surface-1)}
.gnhd .ic{color:var(--text-faint);font-size:14px}
.gnhd .ttl{font-weight:650;font-size:13px}
.gnhd .ctx{font-size:12px;color:var(--text-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

.gnbody{flex:1;min-height:0;overflow-y:auto;padding:10px 12px 14px}
.glist{display:flex;flex-direction:column;gap:8px}

.b-grid{display:flex;flex-direction:column}
.gridtool{display:flex;align-items:center;gap:8px;padding:0 1px 8px}
.gtsource{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text-dim);
  border-radius:var(--radius);padding:4px 9px;font:inherit;font-size:11.5px;font-weight:600;cursor:pointer;white-space:nowrap}
.gtsource:hover{color:var(--text)}
.gtsource .k{color:var(--text-faint);font-weight:500}
.gtsource .car{font-size:9px;opacity:.7}
/* inline source menu — expands in the flow (pushes the grid) so a Vue-Flow node's transform/scroll can't clip it */
.srcmenu{display:flex;flex-direction:column;gap:1px;margin:0 0 8px;padding:5px;max-height:190px;overflow-y:auto;
  border:1px solid var(--border-strong);border-radius:var(--radius-lg);background:var(--surface-1)}
.srcmenu .so{border:0;background:transparent;color:var(--text-dim);font:inherit;font-size:12px;font-weight:600;text-align:left;padding:6px 9px;border-radius:var(--radius);cursor:pointer}
.srcmenu .so:hover{background:var(--surface-3);color:var(--text)}
.srcmenu .so.on{background:var(--nav-active);color:var(--accent)}
.srcmenu .sohd{font-size:9.5px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint);padding:7px 9px 3px}
.cols{display:inline-flex;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.cols button{border:0;border-left:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);font:inherit;font-size:11px;font-weight:700;padding:4px 8px;cursor:pointer}
.cols button:first-child{border-left:0}
.cols button.on{background:var(--nav-active);color:var(--accent)}
.gtcount{margin-left:auto;font-size:11px;color:var(--text-faint);font-variant-numeric:tabular-nums}

.gimgs{display:grid;gap:7px;grid-template-columns:repeat(var(--cols,3),1fr)}
.gthumb{position:relative;aspect-ratio:var(--ar,3/4);border-radius:7px;overflow:hidden;border:1px solid var(--border);cursor:zoom-in;background:var(--surface-3)}
.gthumb:hover{border-color:var(--border-strong)}
.gthumb .im{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.gthumb .star{position:absolute;right:5px;top:5px;border:0;background:transparent;font-size:13px;line-height:1;cursor:pointer;padding:0;
  color:#fff;opacity:0;text-shadow:0 1px 3px rgba(0,0,0,.7)}
.gthumb.fav .star{opacity:1;color:var(--star)}
.gthumb:hover .star{opacity:1}
.gthumb .star:hover{color:var(--star)}

.ghint{font-size:12px;color:var(--text-faint);text-align:center;padding:26px 16px;line-height:1.5;
  border:1px dashed var(--border-strong);border-radius:8px;background:color-mix(in srgb,var(--surface-1) 55%,transparent)}
</style>
