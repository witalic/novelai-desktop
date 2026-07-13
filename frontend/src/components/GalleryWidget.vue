<script setup lang="ts">
// Structured gallery — a canvas node (in the `gallery` zone) that renders the work's gallery-role
// images as a composable stack of typed blocks (design/gallery-widget-mockup.html). Increment 1: a
// single image-grid block over the passed gallery images, with a column control, ★ favourite, and
// click-to-preview. More block types (Section/Heading/Text/Metadata/Divider) land in later increments.
import { computed } from 'vue'
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

    <div class="gnbody nowheel">
      <div class="glist">
        <template v-for="b in blocks" :key="b.id">
          <!-- image grid -->
          <div v-if="b.type === 'grid'" class="b-grid">
            <div class="gridtool nodrag">
              <span class="gtsrc">All images</span>
              <div class="cols">
                <button v-for="n in ([2, 3, 4] as const)" :key="n" class="nodrag" :class="{ on: b.cols === n }"
                  @pointerdown.stop @click.stop="setCols(b, n)">{{ n }}</button>
              </div>
              <span class="gtcount">{{ sourceImages(b.source).length }} images</span>
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
.gtsrc{display:inline-flex;align-items:center;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text-dim);
  border-radius:var(--radius);padding:4px 9px;font-size:11.5px;font-weight:600}
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
