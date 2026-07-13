<script setup lang="ts">
// Structured gallery — a canvas node (in the `gallery` zone) that renders the work's gallery-role
// images as a composable stack of typed blocks (design/gallery-widget-mockup.html). Increment 1: a
// single image-grid block over the passed gallery images, with a column control, ★ favourite, and
// click-to-preview. More block types (Section/Heading/Text/Metadata/Divider) land in later increments.
import { computed, ref } from 'vue'
import { hiddenBlockIds } from './galleryBlocks'
import { newId } from '../vault/ids'
import type { GalleryBlock, GalleryMetaField, ImageNodeData, ZoneNode } from '../types'

// Minimal shape the widget reads off a gallery image node (avoids coupling to Vue Flow's node type).
interface GalleryImage { id: string; data: Partial<ImageNodeData> }

const props = defineProps<{
  data: ZoneNode['data'] // the gallery zone's reactive data (holds `blocks`) — mutated in place, Vue-Flow-tracked
  images: GalleryImage[] // the work's gallery-role image nodes
  title?: string
}>()
const emit = defineEmits<{ favorite: [string]; preview: [string]; remove: [string] }>()

const blocks = computed<GalleryBlock[]>(() => props.data.blocks ?? [])
const total = computed(() => props.images.length)

// Each grid is an album owning an ordered `imageIds` list; resolve them to image nodes for rendering.
const imageById = computed(() => new Map(props.images.map((im) => [im.id, im])))
function gridImages(b: GalleryBlock): GalleryImage[] {
  if (b.type !== 'grid' || !Array.isArray(b.imageIds)) return []
  return b.imageIds.map((id) => imageById.value.get(id)).filter((im): im is GalleryImage => !!im)
}

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
  { type: 'meta', label: 'Metadata', glyph: '≣', hint: 'tags · dates · model · seed' },
  { type: 'divider', label: 'Divider', glyph: '—', hint: 'a thin rule' },
]

// Metadata auto-fields, summarised across the work's gallery images/snapshots (never persisted).
const autoMeta = computed(() => {
  const tags = new Set<string>(), models = new Set<string>(), seeds = new Set<string>(), dims = new Set<string>()
  let minD = '', maxD = ''
  for (const im of props.images) {
    for (const t of im.data.tags || []) tags.add(t)
    const p = (im.data.snapshot?.params || {}) as Record<string, unknown>
    if (p.model) models.add(String(p.model))
    if (p.seed != null) seeds.add(String(p.seed))
    if (p.width && p.height) dims.add(`${p.width}×${p.height}`)
    const d = im.data.created_at || ''
    if (d) { if (!minD || d < minD) minD = d; if (!maxD || d > maxD) maxD = d }
  }
  const day = (s: string) => s.slice(0, 10)
  const one = (set: Set<string>, plural: string) => set.size === 1 ? [...set][0] : set.size ? `${set.size} ${plural}` : '—'
  return {
    tags: [...tags],
    date: minD ? (day(minD) === day(maxD) ? day(minD) : `${day(minD)} – ${day(maxD)}`) : '—',
    model: one(models, 'models'), seed: seeds.size <= 1 ? ([...seeds][0] ?? '—') : 'mixed', dimensions: one(dims, 'sizes'),
  }
})
function metaChips(f: GalleryMetaField): string[] {
  if (f.auto === 'tags') return autoMeta.value.tags
  return Array.isArray(f.value) ? f.value : []
}
function metaText(f: GalleryMetaField): string {
  if (f.auto) return String((autoMeta.value as Record<string, string | string[]>)[f.auto] ?? '—')
  return typeof f.value === 'string' ? f.value : ''
}
function addMetaField(b: GalleryBlock) { if (b.type === 'meta') b.fields.push({ key: 'Field', kind: 'text', value: '' }) }
function removeMetaField(b: GalleryBlock, i: number) { if (b.type === 'meta') b.fields.splice(i, 1) }

// Drag a thumbnail out to the canvas → CanvasBoard spawns a loose (scratch) copy; the gallery keeps the original.
function onThumbDrag(id: string, e: DragEvent) {
  if (e.dataTransfer) { e.dataTransfer.setData('text/plain', `nai-galimg:${id}`); e.dataTransfer.effectAllowed = 'copy' }
}
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
  if (type === 'grid') return { id, type, imageIds: [], cols: 3 }
  if (type === 'meta') return { id, type, fields: [
    { key: 'Tags', kind: 'chips', auto: 'tags' },
    { key: 'Date', kind: 'text', auto: 'date' },
    { key: 'Model', kind: 'mono', auto: 'model' },
    { key: 'Seed', kind: 'mono', auto: 'seed' },
  ] }
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
  dragId.value = id; addOpen.value = null
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
  <div class="gnode" @click="addOpen = null">
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

    <div class="gnbody nowheel" @scroll="addOpen = null">
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

            <!-- image grid — an album owning its images -->
            <div v-else-if="b.type === 'grid'" class="b-grid">
              <div class="gridtool nodrag">
                <div class="cols">
                  <button v-for="n in ([2, 3, 4] as const)" :key="n" class="nodrag" :class="{ on: b.cols === n }"
                    @pointerdown.stop @click.stop="setCols(b, n)">{{ n }}</button>
                </div>
                <span class="gtcount">{{ gridImages(b).length }} image{{ gridImages(b).length === 1 ? '' : 's' }}</span>
              </div>
              <div v-if="gridImages(b).length" class="gimgs" :style="{ '--cols': b.cols }">
                <div v-for="im in gridImages(b)" :key="im.id" class="gthumb nodrag" :class="{ fav: im.data.favorite }"
                  :style="{ '--ar': im.data.ar || (3 / 4) }" draggable="true" @dragstart="onThumbDrag(im.id, $event)"
                  @pointerdown.stop @click.stop="emit('preview', im.data.url || '')">
                  <img class="im" :src="thumbSrc(im.data.url, b.cols)" alt="gallery image" loading="lazy" draggable="false" />
                  <button class="star nodrag" title="Toggle favourite" @pointerdown.stop @click.stop="emit('favorite', im.id)">★</button>
                  <button class="tremove nodrag" title="Remove from this grid (moves to the canvas)" @pointerdown.stop @click.stop="emit('remove', im.id)">✕</button>
                </div>
              </div>
              <div v-else class="ghint">Empty album — keep generations here, or drag images in from another grid.</div>
            </div>

            <!-- heading -->
            <input v-else-if="b.type === 'heading'" class="b-heading nodrag" :class="'h' + b.level"
              v-model="b.text" placeholder="Heading" @pointerdown.stop />
            <!-- text / description -->
            <textarea v-else-if="b.type === 'text'" class="b-text nodrag nowheel" v-model="b.text"
              placeholder="Write a description…" @pointerdown.stop @input="autogrow"></textarea>
            <!-- metadata — a properties strip (auto values summarise the gallery; manual fields are typed) -->
            <div v-else-if="b.type === 'meta'" class="b-meta">
              <div class="metagrid">
                <div v-for="(f, fi) in b.fields" :key="fi" class="mfield">
                  <input v-if="!f.auto" class="fk fk-edit nodrag" v-model="f.key" placeholder="Field" @pointerdown.stop />
                  <div v-else class="fk">{{ f.key }}</div>
                  <div class="fv">
                    <template v-if="f.kind === 'chips'">
                      <span v-for="t in metaChips(f)" :key="t" class="tagc">{{ t }}</span>
                      <span v-if="!metaChips(f).length" class="muted">—</span>
                    </template>
                    <input v-else-if="!f.auto" class="fv-edit nodrag" :value="metaText(f)" placeholder="value"
                      @input="f.value = ($event.target as HTMLInputElement).value" @pointerdown.stop />
                    <span v-else :class="{ mono: f.kind === 'mono' }">{{ metaText(f) }}</span>
                  </div>
                  <button v-if="!f.auto" class="mrm nodrag" title="Remove field" @pointerdown.stop @click.stop="removeMetaField(b, fi)">✕</button>
                </div>
                <button class="maddfield nodrag" @pointerdown.stop @click.stop="addMetaField(b)">＋ Add field</button>
              </div>
            </div>

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

/* metadata — a properties strip */
.b-meta{padding-top:2px}
.metagrid{border:1px solid var(--border);border-radius:8px;background:color-mix(in srgb,var(--surface-2) 55%,transparent);overflow:hidden}
.metagrid .mfield{display:grid;grid-template-columns:110px 1fr auto;gap:8px;align-items:center;padding:7px 11px}
.metagrid .mfield + .mfield{border-top:1px solid var(--border)}
.metagrid .fk{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.3px;color:var(--text-faint)}
.metagrid .fk-edit{border:1px solid transparent;background:transparent;border-radius:4px;padding:2px 4px;text-transform:none;letter-spacing:0;font:inherit;font-size:12px;font-weight:600;color:var(--text);outline:none}
.metagrid .fk-edit:hover{border-color:var(--border)}.metagrid .fk-edit:focus{border-color:var(--accent)}
.metagrid .fv{display:flex;flex-wrap:wrap;gap:5px;align-items:center;color:var(--text-dim);font-size:12px;min-width:0}
.metagrid .fv .muted{color:var(--text-faint)}
.metagrid .fv .mono{font-family:ui-monospace,monospace;font-size:11px}
.metagrid .fv-edit{flex:1;min-width:0;border:1px solid var(--border);background:var(--surface-1);border-radius:4px;padding:3px 6px;font:inherit;font-size:12px;color:var(--text);outline:none}
.metagrid .fv-edit:focus{border-color:var(--accent)}
.metagrid .tagc{font-size:10.5px;color:var(--accent);background:var(--nav-active);border:1px solid color-mix(in srgb,var(--accent) 30%,transparent);border-radius:20px;padding:1px 8px}
.metagrid .mrm{border:0;background:transparent;color:var(--text-faint);font-size:12px;cursor:pointer;padding:0 2px}
.metagrid .mrm:hover{color:var(--danger)}
.metagrid .maddfield{width:100%;border:0;border-top:1px solid var(--border);background:transparent;color:var(--text-faint);font:inherit;font-size:11.5px;font-weight:600;text-align:left;padding:7px 11px;cursor:pointer}
.metagrid .maddfield:hover{color:var(--accent)}
.glhint{font-size:12px;color:var(--text-faint);text-align:center;padding:20px}

.gnft{display:flex;align-items:center;gap:10px;height:34px;flex-shrink:0;padding:0 12px;border-top:1px solid var(--border);background:var(--surface-1);font-size:11.5px;color:var(--text-faint)}
.gnft .sp{flex:1}
.gnft .lbtn{border:0;background:transparent;color:var(--text-dim);font:inherit;font-size:11.5px;font-weight:600;cursor:pointer;display:inline-flex;gap:5px;align-items:center}
.gnft .lbtn:hover{color:var(--accent)}

.gnbody{flex:1;min-height:0;overflow-y:auto;padding:10px 12px 14px}
.glist{display:flex;flex-direction:column;gap:8px}

.b-grid{display:flex;flex-direction:column}
.gridtool{display:flex;align-items:center;gap:8px;padding:0 1px 8px}
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
.gthumb .tremove{position:absolute;left:5px;top:5px;width:18px;height:18px;border:0;border-radius:5px;background:rgba(0,0,0,.55);color:#fff;
  font-size:10px;line-height:1;cursor:pointer;padding:0;opacity:0;display:flex;align-items:center;justify-content:center}
.gthumb:hover .tremove{opacity:1}
.gthumb .tremove:hover{background:var(--danger)}

.ghint{font-size:12px;color:var(--text-faint);text-align:center;padding:26px 16px;line-height:1.5;
  border:1px dashed var(--border-strong);border-radius:8px;background:color-mix(in srgb,var(--surface-1) 55%,transparent)}
</style>
