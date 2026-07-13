<script setup lang="ts">
// Structured gallery — a canvas node (in the `gallery` zone) that renders the work's gallery-role
// images as a composable stack of typed blocks (design/gallery-widget-mockup.html). Increment 1: a
// single image-grid block over the passed gallery images, with a column control, ★ favourite, and
// click-to-preview. More block types (Section/Heading/Text/Metadata/Divider) land in later increments.
import { computed, ref } from 'vue'
import { filterBySource, hiddenBlockIds } from './gallerySource'
import { newId } from '../vault/ids'
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

// ---- compose the block stack (add / delete; drag-reorder lands in a later increment) ----
type BlockType = GalleryBlock['type']
const ADD_TYPES: { type: BlockType; label: string; glyph: string; hint: string }[] = [
  { type: 'section', label: 'Section', glyph: '▤', hint: 'a collapsible group' },
  { type: 'heading', label: 'Heading', glyph: 'H', hint: 'a title' },
  { type: 'text', label: 'Text', glyph: '¶', hint: 'a description / note' },
  { type: 'grid', label: 'Image grid', glyph: '▦', hint: 'images in fixed cells' },
  { type: 'divider', label: 'Divider', glyph: '—', hint: 'a thin rule' },
]
// Blocks after a collapsed section are hidden until the next section (v-show, not v-if, so their DOM
// scroll/focus survives the collapse — UI-design ledger). Range logic is unit-tested in gallerySource.
const hiddenIds = computed(() => hiddenBlockIds(blocks.value))
const addOpen = ref<'head' | 'foot' | null>(null) // which ＋ Add block opened the menu — transient
function toggleAdd(where: 'head' | 'foot') { addOpen.value = addOpen.value === where ? null : where }
function newBlock(type: BlockType): GalleryBlock {
  const id = newId('gb')
  if (type === 'section') return { id, type, title: 'Section', collapsed: false }
  if (type === 'heading') return { id, type, text: 'Heading', level: 2 }
  if (type === 'text') return { id, type, text: '' }
  if (type === 'grid') return { id, type, source: 'all', cols: 3 }
  return { id, type: 'divider' }
}
function addBlock(type: BlockType) {
  ;(props.data.blocks ??= []).push(newBlock(type)) // in-place → tracked + autosaved
  addOpen.value = null
}
function deleteBlock(id: string) {
  const arr = props.data.blocks
  const i = arr ? arr.findIndex((b) => b.id === id) : -1
  if (arr && i >= 0) arr.splice(i, 1)
}
// Inline edit for heading/text — v-model mutates the block in place; no contenteditable cursor issues.
function autogrow(e: Event) {
  const el = e.target as HTMLTextAreaElement
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

// ---- drag-to-reorder the block stack (HTML5 drag off a grip; the node itself never moves) ----
const dragId = ref<string | null>(null)
const dragOverId = ref<string | null>(null)
function onBlkDragStart(id: string, e: DragEvent) {
  dragId.value = id; addOpen.value = null; sourceOpen.value = null
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'
}
function onBlkDragOver(id: string) { if (dragId.value && id !== dragId.value) dragOverId.value = id }
function onBlkDragEnd() { dragId.value = null; dragOverId.value = null }
function onBlkDrop(targetId: string) {
  const from = dragId.value
  dragId.value = null; dragOverId.value = null
  const arr = props.data.blocks
  if (!from || from === targetId || !arr) return
  const fi = arr.findIndex((b) => b.id === from)
  if (fi < 0 || !arr.some((b) => b.id === targetId)) return
  const [moved] = arr.splice(fi, 1)
  arr.splice(arr.findIndex((b) => b.id === targetId), 0, moved) // drop before the target → in-place, persists
}
</script>

<template>
  <div class="gnode" @click="addOpen = null; sourceOpen = null">
    <div class="gnhd">
      <span class="ic">▦</span>
      <span class="ttl">Gallery</span>
      <span v-if="title" class="ctx">· {{ title }}</span>
      <span class="ctx">· {{ total }} image{{ total === 1 ? '' : 's' }}</span>
      <span class="hsp"></span>
      <div class="addwrap">
        <button class="addbtn nodrag" @pointerdown.stop @click.stop="toggleAdd('head')"><span>＋</span> Add block</button>
        <div v-if="addOpen === 'head'" class="addmenu nodrag" @pointerdown.stop @click.stop>
          <button v-for="t in ADD_TYPES" :key="t.type" class="amrow" @click.stop="addBlock(t.type)">
            <span class="gl">{{ t.glyph }}</span><span class="aml">{{ t.label }}<small>{{ t.hint }}</small></span>
          </button>
        </div>
      </div>
    </div>

    <div class="gnbody nowheel" @scroll="sourceOpen = null; addOpen = null">
      <div class="glist">
        <template v-for="b in blocks" :key="b.id">
          <div v-show="!hiddenIds.has(b.id)" class="blk" :class="['blk-' + b.type, { drop: dragOverId === b.id, dragging: dragId === b.id }]"
            @dragover.prevent="onBlkDragOver(b.id)" @drop.prevent="onBlkDrop(b.id)" @dragleave="dragOverId = null">
            <span class="bgrip nodrag" title="Drag to reorder" draggable="true"
              @pointerdown.stop @dragstart="onBlkDragStart(b.id, $event)" @dragend="onBlkDragEnd">⠿</span>
            <div class="bacts nodrag">
              <button class="del nodrag" title="Delete block" @pointerdown.stop @click.stop="deleteBlock(b.id)">🗑</button>
            </div>

            <!-- section — a collapsible group boundary -->
            <div v-if="b.type === 'section'" class="b-section">
              <button class="sectw nodrag" :title="b.collapsed ? 'Expand' : 'Collapse'" @pointerdown.stop @click.stop="b.collapsed = !b.collapsed">{{ b.collapsed ? '▸' : '▾' }}</button>
              <input class="secname nodrag" v-model="b.title" placeholder="Section" @pointerdown.stop />
            </div>

            <!-- image grid -->
            <div v-else-if="b.type === 'grid'" class="b-grid">
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

            <!-- heading -->
            <input v-else-if="b.type === 'heading'" class="b-heading nodrag" :class="'h' + b.level"
              v-model="b.text" placeholder="Heading" @pointerdown.stop />
            <!-- text / description -->
            <textarea v-else-if="b.type === 'text'" class="b-text nodrag nowheel" v-model="b.text"
              placeholder="Write a description…" @pointerdown.stop @input="autogrow"></textarea>
            <!-- divider -->
            <div v-else-if="b.type === 'divider'" class="b-divider"><div class="ln"></div></div>
          </div>
        </template>
        <div v-if="!blocks.length" class="glhint">Empty gallery — add a block below to start.</div>
      </div>
    </div>

    <div class="gnft">
      <span>{{ blocks.length }} block{{ blocks.length === 1 ? '' : 's' }}</span>
      <span class="sp"></span>
      <div class="addwrap up">
        <button class="lbtn nodrag" @pointerdown.stop @click.stop="toggleAdd('foot')">＋ Add block</button>
        <div v-if="addOpen === 'foot'" class="addmenu up nodrag" @pointerdown.stop @click.stop>
          <button v-for="t in ADD_TYPES" :key="t.type" class="amrow" @click.stop="addBlock(t.type)">
            <span class="gl">{{ t.glyph }}</span><span class="aml">{{ t.label }}<small>{{ t.hint }}</small></span>
          </button>
        </div>
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
.gnhd .hsp{flex:1}

/* ＋ Add block menu (header + footer) — absolute within the node (its overflow is only clipped at the node edge) */
.addwrap{position:relative}
.addbtn{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);padding:4px 10px;font:inherit;font-size:11.5px;font-weight:600;cursor:pointer;white-space:nowrap}
.addbtn:hover{color:var(--accent);border-color:var(--accent)}
.addmenu{position:absolute;right:0;top:calc(100% + 5px);z-index:30;min-width:190px;border:1px solid var(--border-strong);border-radius:var(--radius-lg);background:var(--surface-1);box-shadow:0 10px 30px rgba(0,0,0,.45);padding:5px}
.addmenu.up{top:auto;bottom:calc(100% + 5px)}
.addmenu .amrow{display:flex;align-items:center;gap:9px;width:100%;border:0;background:transparent;color:var(--text);font:inherit;font-size:12.5px;font-weight:600;padding:7px 9px;border-radius:var(--radius);cursor:pointer;text-align:left}
.addmenu .amrow:hover{background:var(--surface-3)}
.addmenu .amrow .gl{width:20px;height:20px;border-radius:5px;background:var(--surface-3);border:1px solid var(--border);display:inline-flex;align-items:center;justify-content:center;font-size:12px;color:var(--text-dim);flex-shrink:0}
.addmenu .amrow .aml{display:flex;flex-direction:column;line-height:1.25}
.addmenu .amrow .aml small{font-weight:500;color:var(--text-faint);font-size:11px}

/* block wrapper: a hover grip (drag-reorder) on the left, a hover delete on the right */
.blk{position:relative;border-radius:8px;padding:3px 6px 3px 22px}
.blk:hover{background:color-mix(in srgb,var(--surface-2) 45%,transparent)}
.blk-grid:hover,.blk-divider:hover{background:transparent}
.blk.dragging{opacity:.45}
.blk.drop{box-shadow:0 -2px 0 0 var(--accent)}
.bgrip{position:absolute;left:4px;top:8px;width:14px;text-align:center;color:var(--text-faint);font-size:12px;line-height:1;cursor:grab;opacity:0;user-select:none}
.blk:hover .bgrip{opacity:1}
.bgrip:active{cursor:grabbing}
.bacts{position:absolute;right:4px;top:4px;display:none;z-index:4}
.blk:hover .bacts{display:block}
.bacts .del{border:1px solid var(--border);background:var(--surface-1);color:var(--text-faint);border-radius:5px;height:22px;min-width:22px;font-size:11px;cursor:pointer;padding:0 4px}
.bacts .del:hover{color:var(--danger);border-color:var(--danger)}
.blk-grid .gridtool{padding-right:24px} /* clear the hover delete over the count */

/* section — a group boundary bar */
.b-section{display:flex;align-items:center;gap:8px;padding:8px 2px 7px;border-bottom:1px solid var(--border);margin-top:6px}
.b-section .sectw{border:0;background:transparent;color:var(--text-faint);font-size:11px;cursor:pointer;padding:0;width:14px;flex-shrink:0}
.b-section .sectw:hover{color:var(--text)}
.b-section .secname{flex:1;min-width:0;border:0;background:transparent;color:var(--text);font:inherit;font-size:13.5px;font-weight:700;outline:none;padding:2px 4px;border-radius:4px}
.b-section .secname:hover{background:var(--surface-3)}
.b-section .secname:focus{background:var(--surface-1)}
.b-section .secname::placeholder{color:var(--text-faint);font-weight:600}

/* copy blocks — borderless inline editors */
.b-heading{width:100%;border:0;background:transparent;color:var(--text);font:inherit;font-weight:750;outline:none;padding:6px 30px 4px 2px}
.b-heading.h1{font-size:19px;letter-spacing:-.01em}
.b-heading.h2{font-size:15px}
.b-heading::placeholder{color:var(--text-faint)}
.b-text{display:block;width:100%;border:0;background:transparent;color:var(--text-dim);font:inherit;font-size:12.5px;line-height:1.6;outline:none;resize:none;overflow:hidden;min-height:22px;padding:2px 30px 2px 2px}
.b-text::placeholder{color:var(--text-faint)}
.b-divider{padding:9px 2px}
.b-divider .ln{height:1px;background:var(--border)}
.glhint{font-size:12px;color:var(--text-faint);text-align:center;padding:20px}

.gnft{display:flex;align-items:center;gap:10px;height:34px;flex-shrink:0;padding:0 12px;border-top:1px solid var(--border);background:var(--surface-1);font-size:11.5px;color:var(--text-faint)}
.gnft .sp{flex:1}
.gnft .lbtn{border:0;background:transparent;color:var(--text-dim);font:inherit;font-size:11.5px;font-weight:600;cursor:pointer;display:inline-flex;gap:5px;align-items:center}
.gnft .lbtn:hover{color:var(--accent)}

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
