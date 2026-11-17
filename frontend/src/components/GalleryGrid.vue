<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { justify, visibleRows } from '../gallery/justify'
import { dayHeading } from '../gallery/query'
import { thumbSrc } from '../vault/thumbs'
import type { GalleryImage } from '../types'

// The Gallery's grid (design/global-gallery.html §1): images in their true shape, in justified rows under a heading
// per day. Only the rows on screen (and a margin) are drawn; nearing the end asks for the next page.
const props = defineProps<{ items: GalleryImage[]; complete: boolean; rowHeight: number }>() // rowHeight: the tile size
const emit = defineEmits<{ more: []; open: [GalleryImage]; favorite: [GalleryImage] }>()

const GAP = 5
const DAY_H = 38
const AHEAD = 700 // px drawn beyond the screen, each way
const MORE_WITHIN = 1600 // px from the end that asks for the next page

const scroller = ref<HTMLElement | null>(null)
const width = ref(0)
const viewH = ref(0)
const top = ref(0)
let ro: ResizeObserver | null = null
onMounted(() => {
  const el = scroller.value
  if (!el) return
  width.value = el.clientWidth - 32 // the padding, 16px a side
  viewH.value = el.clientHeight
  ro = new ResizeObserver(() => { width.value = el.clientWidth - 32; viewH.value = el.clientHeight })
  ro.observe(el)
})
onBeforeUnmount(() => ro?.disconnect())

const byId = computed(() => new Map(props.items.map((it) => [it.image_id, it])))
const layout = computed(() => justify(
  props.items.map((it) => ({ id: it.image_id, ar: it.ar || 2 / 3, day: dayHeading(it.created_at) })),
  Math.max(200, width.value), { rowHeight: props.rowHeight, gap: GAP, dayHeight: DAY_H, complete: props.complete }))
const rows = computed(() => visibleRows(layout.value.rows, top.value - AHEAD, top.value + viewH.value + AHEAD))

function onScroll() { top.value = scroller.value?.scrollTop ?? 0 }
// Near the end — or a first page too short to scroll — the next page joins.
watch([top, viewH, () => layout.value.height, () => props.complete], () => {
  if (!props.complete && viewH.value && top.value + viewH.value > layout.value.height - MORE_WITHIN) emit('more')
})

function place(it: GalleryImage): string {
  const a = it.album
  return a ? a.section || a.heading || `album ${a.number}` : ''
}
</script>

<template>
  <div ref="scroller" class="gscroll" @scroll.passive="onScroll">
    <div class="gcanvas" :style="{ height: `${layout.height}px` }">
      <template v-for="r in rows" :key="r.key">
        <div v-if="r.kind === 'day'" class="day" :style="{ top: `${r.top}px`, height: `${r.height}px` }">
          {{ r.label }}<span v-if="r.count !== null" class="n">{{ r.count }}</span>
        </div>
        <div v-else class="row" :style="{ top: `${r.top}px`, height: `${r.height}px` }">
          <div v-for="t in r.tiles" :key="t.id" class="t" :style="{ left: `${t.x}px`, width: `${t.w}px` }"
            role="button" tabindex="0" @click="emit('open', byId.get(t.id)!)" @keyup.enter="emit('open', byId.get(t.id)!)">
            <img :src="thumbSrc(byId.get(t.id)!.url, t.w)" alt="" loading="lazy" decoding="async" draggable="false" />
            <button class="fv" :class="{ on: byId.get(t.id)!.favorite }"
              :title="byId.get(t.id)!.favorite ? 'Unfavorite' : 'Favorite'" @click.stop="emit('favorite', byId.get(t.id)!)">
              {{ byId.get(t.id)!.favorite ? '★' : '☆' }}
            </button>
            <span v-if="!byId.get(t.id)!.album" class="us">Unsorted</span>
            <div class="cap">{{ byId.get(t.id)!.work_title }}<span v-if="place(byId.get(t.id)!)"> › {{ place(byId.get(t.id)!) }}</span></div>
          </div>
        </div>
      </template>
    </div>
    <slot name="end" />
  </div>
</template>

<style scoped>
.gscroll { flex: 1; min-height: 0; overflow-y: auto; padding: 0 16px 16px; }
.gcanvas { position: relative; }
.day { position: absolute; left: 0; right: 0; display: flex; align-items: flex-end; gap: 8px; padding-bottom: 8px;
  font-size: 13px; font-weight: 700; color: var(--text); }
.day .n { font-size: 11.5px; font-weight: 500; color: var(--text-faint); }
.row { position: absolute; left: 0; right: 0; }
.t { position: absolute; top: 0; bottom: 0; border-radius: 5px; overflow: hidden; background: var(--surface-3); cursor: zoom-in; }
.t img { width: 100%; height: 100%; object-fit: cover; display: block; }
.t:hover, .t:focus-visible { outline: 2px solid var(--border-strong); outline-offset: -2px; }
.fv { position: absolute; right: 5px; top: 4px; width: 26px; height: 26px; border: 0; border-radius: 6px; background: transparent;
  color: #fff; font-size: 15px; line-height: 1; cursor: pointer; text-shadow: 0 1px 3px rgba(0, 0, 0, .7); opacity: 0; }
.fv.on { opacity: 1; color: var(--star); }
.t:hover .fv { opacity: 1; }
.fv:hover { background: rgba(0, 0, 0, .35); }
.us { position: absolute; left: 6px; bottom: 6px; font-size: 9.5px; font-weight: 700; letter-spacing: .2px; color: #fff;
  background: rgba(0, 0, 0, .55); border: 1px solid rgba(255, 255, 255, .35); border-radius: 20px; padding: 0 6px; }
.cap { position: absolute; left: 0; right: 0; bottom: 0; padding: 16px 8px 6px; font-size: 11px; color: #fff;
  background: linear-gradient(transparent, rgba(0, 0, 0, .72)); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  opacity: 0; transition: opacity .12s; }
.cap span { opacity: .78; }
.t:hover .cap { opacity: 1; }
.t:hover .us { display: none; }
</style>
