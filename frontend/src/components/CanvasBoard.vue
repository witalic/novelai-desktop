<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { NodeResizer } from '@vue-flow/node-resizer'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/node-resizer/dist/style.css'
import { saveDownloads } from '../api'
import { useToast } from '../composables/useToast'
import type { GenResult } from '../types'

const toast = useToast()
const props = defineProps<{ drafts: GenResult[]; busy: boolean; error: string; preview: string }>()
const emit = defineEmits<{ generate: [{ positive: string; negative: string }]; take: [] }>()

const {
  nodes, addNodes, findNode, onNodeDragStop, getIntersectingNodes, fitView, viewport, screenToFlowCoordinate,
  onNodeContextMenu, onSelectionContextMenu, onPaneContextMenu,
} = useVueFlow()

// The "station" is one coupled node: [ Output | Positive / Negative lanes ] + header + meta.
// Internal areas are ratio-driven (data.outputRatio, data.posRatio) so they scale on resize + splitters.
const STATION = 'station'
const HEADER = 44
const META = 66

const CATS: Record<string, string> = {
  style: '#6e5dc6', character: '#0c66e4', pose: '#ae4787', environment: '#1f845a',
  lighting: '#b65c02', camera: '#12b5a6', outfit: '#d4537e', negative: '#e2483d', custom: '#738496',
}
const catColor = (c: string) => CATS[c] ?? CATS.custom

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function block(id: string, category: string, name: string, text: string, polarity: string, parent: string | undefined, pos: { x: number; y: number }, expanded = false): any {
  return { id, type: 'block', position: pos, parentNode: parent, zIndex: 2, style: { width: '176px' }, data: { category, name, text, polarity, expanded } }
}

const compBlocks = computed(() => nodes.value.filter((n) => n.type === 'block' && n.parentNode === STATION))
const composed = computed(() => {
  const pick = (neg: boolean) => compBlocks.value
    .filter((b) => (b.data.polarity === 'negative') === neg)
    .slice().sort((a, b) => a.position.x - b.position.x)
    .map((b) => String(b.data.text || '').trim()).filter(Boolean)
  return { positive: pick(false).join(', '), negative: pick(true).join(', ') }
})
const tokenEstimate = computed(() => Math.ceil((composed.value.positive.length + composed.value.negative.length) / 4))

let zoneSeq = 0
let blockSeq = 0
let takeCounter = 0
const flowRef = ref<HTMLElement | null>(null)
const topSelected = ref(false)

function toFlow(clientX: number, clientY: number) {
  if (typeof screenToFlowCoordinate === 'function') return screenToFlowCoordinate({ x: clientX, y: clientY })
  const rect = flowRef.value?.getBoundingClientRect()
  const vp = viewport.value
  return { x: (clientX - (rect?.left ?? 0) - vp.x) / vp.zoom, y: (clientY - (rect?.top ?? 0) - vp.y) / vp.zoom }
}

// Drag the top-of-stack image out of the output slot onto the canvas to keep it (materialise a node).
function onDraftDragStart(e: DragEvent) {
  if (!props.drafts.length || !e.dataTransfer) return
  e.dataTransfer.effectAllowed = 'move'
  e.dataTransfer.setData('text/plain', 'nai-draft')
}
function onCanvasDrop(e: DragEvent) {
  e.preventDefault()
  const draft = props.drafts[0]
  if (!draft) return
  const pos = toFlow(e.clientX, e.clientY)
  const ar = (draft.params.width || 832) / (draft.params.height || 1216)
  const w = ar >= 1 ? 180 : Math.round(180 * ar)
  const h = ar >= 1 ? Math.round(180 / ar) : 180
  takeCounter += 1
  addNodes([{ id: `taken-${takeCounter}`, type: 'image', position: { x: pos.x - w / 2, y: pos.y - h / 2 }, zIndex: 3, style: { width: `${w}px`, height: `${h}px` }, data: { url: draft.url } }])
  emit('take')
}

// Shift an image right until it no longer overlaps a sibling image (same parent), using real sizes.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function dims(n: any) {
  return { w: n.dimensions?.width || Number.parseFloat(n.style?.width) || 116, h: n.dimensions?.height || Number.parseFloat(n.style?.height) || 168 }
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function nudgeIfOverlapping(live: any) {
  const { w: lw, h: lh } = dims(live)
  const others = nodes.value.filter((n) => n.type === 'image' && n.id !== live.id && n.parentNode === live.parentNode)
  const pos = { x: live.position.x, y: live.position.y }
  const hits = (p: { x: number; y: number }) => others.some((o) => {
    const { w, h } = dims(o)
    return Math.abs((o.position.x + w / 2) - (p.x + lw / 2)) < (w + lw) / 2 - 2
      && Math.abs((o.position.y + h / 2) - (p.y + lh / 2)) < (h + lh) / 2 - 2
  })
  let guard = 0
  while (guard < 80 && hits(pos)) { pos.x += 16; guard += 1 }
  live.position = pos
}
function nudgeById(id: string) {
  const live = findNode(id)
  if (live) nudgeIfOverlapping(live)
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

// Reflow the station's blocks so each keeps its relative position WITHIN its polarity lane after the
// station resizes or a splitter moves — a negative block never drifts into the positive region.
function relayoutBlocks() {
  const st = findNode(STATION)
  if (!st) return
  const h = st.dimensions.height, stW = st.dimensions.width
  const outputW = (st.data.outputRatio ?? 0.3) * stW
  const compW = stW - outputW
  const laneBoundary = HEADER + (st.data.posRatio ?? 0.5) * (h - HEADER - META)
  for (const n of nodes.value) {
    if (n.type !== 'block' || n.parentNode !== STATION) continue
    const neg = n.data.polarity === 'negative'
    const laneTop = neg ? laneBoundary : HEADER
    const laneBot = neg ? (h - META) : laneBoundary
    const bd = dims(n)
    n.position = {
      x: outputW + (n.data.xFrac ?? 0.06) * Math.max(0, compW - bd.w),
      y: laneTop + (n.data.laneFrac ?? 0.12) * Math.max(0, (laneBot - laneTop) - bd.h),
    }
  }
}

onMounted(() => {
  addNodes([
    { id: STATION, type: 'station', position: { x: 40, y: 40 }, data: { outputRatio: 0.3, posRatio: 0.5 }, zIndex: 0, style: { width: '824px', height: '460px' } },
    block('b1', 'style', 'Cinematic', 'cinematic lighting, masterpiece, best quality', 'positive', STATION, { x: 284, y: 60 }),
    block('b2', 'character', 'Silver-haired girl', '1girl, silver hair, blue eyes', 'positive', STATION, { x: 470, y: 60 }),
    block('b3', 'negative', 'Low quality', 'lowres, bad anatomy, worst quality', 'negative', STATION, { x: 284, y: 268 }),
    block('b4', 'environment', 'Night city', 'night, city lights, bokeh, depth of field', 'positive', undefined, { x: 900, y: 60 }),
    block('b5', 'pose', 'Portrait pose', 'upper body, looking at viewer', 'positive', undefined, { x: 900, y: 200 }),
  ])
})

// Settle EVERY dragged node (multi-select moves a whole set) — not just the grabbed one.
onNodeDragStop(({ nodes: dragged, node }) => {
  const set = dragged && dragged.length ? dragged : [node]
  for (const n of set) settleNode(n)
})

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function settleNode(node: any) {
  const live = findNode(node.id)
  if (!live) return
  const st = findNode(STATION)
  const rel = st ? { x: node.computedPosition.x - st.computedPosition.x, y: node.computedPosition.y - st.computedPosition.y } : { x: 0, y: 0 }
  const overStation = getIntersectingNodes(node).some((n) => n.id === STATION)
  const h = st ? st.dimensions.height : 460
  const stW = st ? st.dimensions.width : 824
  const outputW = (st?.data.outputRatio ?? 0.3) * stW
  const laneBoundary = HEADER + (st?.data.posRatio ?? 0.5) * (h - HEADER - META)

  if (node.type === 'block') {
    // Blocks belong only in the composition side (right of the Output column).
    if (overStation && rel.x >= outputW && rel.y >= HEADER && rel.y <= h - META) {
      if (live.parentNode !== STATION) live.position = rel
      live.parentNode = STATION
      live.data.polarity = live.position.y < laneBoundary ? 'positive' : 'negative'
      const neg = live.data.polarity === 'negative'
      const lTop = neg ? laneBoundary : HEADER
      const lBot = neg ? (h - META) : laneBoundary
      const bd = dims(live)
      live.data.xFrac = clamp01((live.position.x - outputW) / Math.max(1, (stW - outputW) - bd.w))
      live.data.laneFrac = clamp01((live.position.y - lTop) / Math.max(1, (lBot - lTop) - bd.h))
    } else if (live.parentNode) {
      live.position = { x: node.computedPosition.x, y: node.computedPosition.y }
      live.parentNode = undefined
    }
  } else if (node.type === 'image') {
    const grp = getIntersectingNodes(node).find((n) => n.type === 'zone')
    if (grp) {
      const gp = grp.computedPosition
      live.parentNode = grp.id
      live.position = { x: node.computedPosition.x - gp.x, y: node.computedPosition.y - gp.y }
    } else if (live.parentNode) {
      live.position = { x: node.computedPosition.x, y: node.computedPosition.y }
      live.parentNode = undefined
    }
    nudgeIfOverlapping(live)
  }
}

function addGroup() {
  zoneSeq += 1
  addNodes([{ id: `zone-${zoneSeq}`, type: 'zone', position: { x: 920 + zoneSeq * 30, y: 340 },
    data: { name: 'New group', description: '', tags: '' }, style: { width: '300px', height: '220px' }, zIndex: 0 }])
}
function addBlock() {
  blockSeq += 1
  addNodes([block(`bc-${blockSeq}`, 'custom', 'New block', '', 'positive', undefined, { x: 900, y: 330 + blockSeq * 24 }, true)])
}
function doGenerate() { emit('generate', composed.value) }

// Right-click context menu — download image node(s). Works on a single image or a multi-selection of images.
const ctx = ref<{ show: boolean; x: number; y: number; images: string[] }>({ show: false, x: 0, y: 0, images: [] })
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function openCtx(event: MouseEvent, targets: any[]) {
  const images = targets.map((t) => t.data?.url).filter(Boolean)
  if (!images.length) { ctx.value.show = false; return }
  ctx.value = { show: true, x: event.clientX, y: event.clientY, images }
}
function closeCtx() { ctx.value.show = false }
async function downloadImages() {
  const payloads = ctx.value.images.map((url) => url.replace(/^data:[^,]+,/, ''))
  const n = payloads.length
  closeCtx()
  try {
    await saveDownloads(payloads)
    toast.push(`Saved ${n} image${n > 1 ? 's' : ''} to Downloads`, 'ok')
  } catch (e) {
    toast.push(e instanceof Error ? e.message : 'Download failed', 'err')
  }
}
onNodeContextMenu(({ event, node }) => {
  event.preventDefault()
  if (node.type !== 'image') { closeCtx(); return }
  const selected = nodes.value.filter((n) => n.type === 'image' && n.selected)
  openCtx(event as MouseEvent, node.selected && selected.length > 1 ? selected : [node])
})
onSelectionContextMenu(({ event, nodes: sel }) => {
  event.preventDefault()
  const imgs = sel.filter((n) => n.type === 'image')
  if (imgs.length && imgs.length === sel.length) openCtx(event as MouseEvent, imgs)
  else closeCtx()
})
onPaneContextMenu(() => closeCtx())

// Drag an internal splitter to adjust the Output↔composition (v) or Positive↔Negative (h) ratio.
function startSplit(kind: 'v' | 'h', e: MouseEvent) {
  const st = findNode(STATION)
  if (!st) return
  const start = kind === 'v' ? e.clientX : e.clientY
  const startRatio = kind === 'v' ? (st.data.outputRatio ?? 0.3) : (st.data.posRatio ?? 0.5)
  const span = kind === 'v' ? st.dimensions.width : (st.dimensions.height - HEADER - META)
  const onMove = (ev: MouseEvent) => {
    const zoom = viewport.value?.zoom ?? 1
    const delta = ((kind === 'v' ? ev.clientX : ev.clientY) - start) / zoom
    const r = Math.min(0.75, Math.max(0.15, startRatio + delta / span))
    if (kind === 'v') st.data.outputRatio = r
    else st.data.posRatio = r
    relayoutBlocks()
  }
  const onUp = () => {
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

// Auto-grow a block to fit its text on expand; restore the collapsed size on collapse.
function toggleExpand(id: string) {
  const live = findNode(id)
  if (!live) return
  const d = live.data
  if (!d.expanded) {
    d._cw = live.style?.width
    d._ch = live.style?.height
    const len = String(d.text || '').length
    const height = Math.min(340, Math.max(132, 66 + Math.ceil((len + 1) / 24) * 18))
    live.style = { width: '246px', height: `${height}px` }
    d.expanded = true
  } else {
    live.style = d._ch ? { width: d._cw || '176px', height: d._ch } : { width: d._cw || '176px' }
    d.expanded = false
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function startName(data: any, e: MouseEvent) {
  data.editing = true
  requestAnimationFrame(() => {
    const input = (e.target as HTMLElement).closest('.block')?.querySelector('.bname-input') as HTMLInputElement | null
    input?.focus()
    input?.select()
  })
}
</script>

<template>
  <section class="canvas">
    <div class="projbar">
      <input class="projname" placeholder="Untitled project" />
      <div class="projmeta"><span class="dot">·</span> 2026-07-07</div>
      <span v-if="error" class="chip err">{{ error }}</span>
      <div class="spacer"></div>
      <button class="barbtn" @click="addBlock"><span>＋</span> Block</button>
      <button class="barbtn" @click="addGroup"><span>⊞</span> Group</button>
      <button class="barbtn" @click="fitView()"><span>⤢</span> Fit</button>
    </div>

    <div class="flowwrap" ref="flowRef" @drop="onCanvasDrop" @dragover.prevent>
      <VueFlow :min-zoom="0.2" :max-zoom="2.5" :delete-key-code="null" :snap-to-grid="true" :snap-grid="[16, 16]"
               :multi-selection-key-code="['Control', 'Meta']" :selection-key-code="'Shift'"
               :zoom-on-double-click="false" style="height:100%;width:100%">
        <Background pattern-color="var(--border-strong)" :gap="18" :size="1.2" />
        <Controls position="bottom-left" :show-interactive="false" />

        <template #node-station="{ data, selected }">
          <NodeResizer :min-width="640" :min-height="360" :is-visible="selected" color="var(--accent)" @resize="relayoutBlocks()" />
          <div class="station">
            <div class="sthd">
              <span class="sttitle">Generation</span>
              <span v-if="busy" class="chip busy"><span class="spinner"></span> Generating…</span>
              <div class="spacer"></div>
              <button class="gzgen nodrag" :disabled="busy || !composed.positive" @pointerdown.stop @click.stop="doGenerate">Generate</button>
            </div>
            <div class="stbody">
              <div class="stoutput" :style="{ flexGrow: data.outputRatio ?? 0.3 }">
                <div class="colhd">Output</div>
                <div class="outbody">
                  <img v-if="busy && preview" class="liveprev" :src="preview" alt="generating preview" />
                  <div v-else-if="drafts.length" class="topwrap nodrag" :class="{ selected: topSelected }" draggable="true"
                    @dragstart="onDraftDragStart" @click="topSelected = !topSelected" title="Drag onto the canvas to keep">
                    <img class="topimg" :src="drafts[0].url" alt="latest generation" draggable="false" />
                    <span class="stackbadge">{{ drafts.length }} in stack</span>
                    <span class="draghint">⤴ drag to keep</span>
                  </div>
                  <span v-else-if="!busy" class="outhint">Generated images appear here — drag them out to keep.</span>
                </div>
              </div>
              <div class="vsplit nodrag" title="Drag to resize" @mousedown.stop.prevent="startSplit('v', $event)"></div>
              <div class="stcomp" :style="{ flexGrow: 1 - (data.outputRatio ?? 0.3) }">
                <div class="lane pos" :style="{ flexGrow: data.posRatio ?? 0.5 }"><span class="lanelbl">Positive</span></div>
                <div class="hsplit nodrag" title="Drag to resize" @mousedown.stop.prevent="startSplit('h', $event)"></div>
                <div class="lane neg" :style="{ flexGrow: 1 - (data.posRatio ?? 0.5) }"><span class="lanelbl">Negative</span></div>
              </div>
            </div>
            <div class="stmeta nowheel">
              <div class="mrow"><b>+</b> {{ composed.positive || '—' }}</div>
              <div class="mrow neg"><b>−</b> {{ composed.negative || '—' }}</div>
              <div class="mtok">≈ {{ tokenEstimate }} tokens</div>
            </div>
          </div>
        </template>

        <template #node-image="{ id, data, selected }">
          <NodeResizer :min-width="72" :is-visible="selected" :keep-aspect-ratio="true" color="var(--accent)" @resize-end="nudgeById(id)" />
          <div class="imgnode" :class="{ selected, isnew: data.isNew }">
            <span v-if="data.isNew" class="new">NEW</span>
            <img v-if="data.url" :src="data.url" alt="generation" draggable="false" />
          </div>
        </template>

        <template #node-zone="{ data, selected }">
          <NodeResizer :min-width="200" :min-height="150" :is-visible="selected" color="var(--accent)" />
          <div class="zonenode" :class="{ selected }">
            <div class="zonehd"><input class="zonename nodrag" v-model="data.name" placeholder="Group name" /><span class="pill">group</span></div>
            <div v-if="selected" class="zonemeta nodrag nowheel">
              <textarea class="mini-in" v-model="data.description" placeholder="Description…"></textarea>
              <input class="mini-in" v-model="data.tags" placeholder="tags, comma-separated" />
            </div>
          </div>
        </template>

        <template #node-block="{ id, data, selected }">
          <NodeResizer :min-width="150" :min-height="34" :is-visible="selected" color="var(--accent)" />
          <div class="block" :class="{ neg: data.polarity === 'negative', expanded: data.expanded }" :style="{ '--cat': catColor(data.category) }">
            <div class="bhd">
              <span class="cdot"></span>
              <input v-if="data.editing" class="bname bname-input nodrag" v-model="data.name"
                @blur="data.editing = false" @keyup.enter="data.editing = false" @keyup.esc="data.editing = false" />
              <span v-else class="bname bname-text" title="double-click to rename" @dblclick.stop="startName(data, $event)">{{ data.name }}</span>
              <button class="bicon nodrag" :title="data.polarity === 'negative' ? 'negative' : 'positive'"
                @click="data.polarity = data.polarity === 'negative' ? 'positive' : 'negative'">{{ data.polarity === 'negative' ? '−' : '＋' }}</button>
              <button class="bicon nodrag" @click="toggleExpand(id)">{{ data.expanded ? '▾' : '▸' }}</button>
            </div>
            <textarea v-if="data.expanded" class="btext nodrag nowheel" v-model="data.text" placeholder="tags…"></textarea>
            <div v-else class="bprev">{{ data.text || 'empty' }}</div>
          </div>
        </template>
      </VueFlow>
    </div>

    <template v-if="ctx.show">
      <div class="ctxback" @click="closeCtx" @contextmenu.prevent="closeCtx"></div>
      <div class="ctxmenu" :style="{ left: ctx.x + 'px', top: ctx.y + 'px' }">
        <button @click="downloadImages"><span>⤓</span> Download {{ ctx.images.length > 1 ? `${ctx.images.length} images` : 'image' }}</button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.canvas{display:flex;flex-direction:column;min-width:0;background:var(--bg)}
.projbar{display:flex;align-items:center;gap:10px;padding:11px 20px;border-bottom:1px solid var(--border);flex-shrink:0}
.projname{font-size:16px;font-weight:600;color:var(--text);background:transparent;border:1px solid transparent;border-radius:var(--radius);padding:5px 8px;width:min(300px,40%)}
.projname:hover{border-color:var(--border)}.projname:focus{border-color:var(--accent);outline:none;background:var(--surface-2)}
.projname::placeholder{color:var(--text-faint);font-weight:500}
.projmeta{font-size:12px;color:var(--text-faint)}.dot{opacity:.5}.spacer{flex:1}
.chip{display:inline-flex;align-items:center;gap:7px;font-size:12px;font-weight:500;padding:4px 9px;border-radius:20px;background:var(--surface-2);border:1px solid var(--border);color:var(--text-dim)}
.chip.err{color:#e2483d;border-color:color-mix(in srgb,#e2483d 40%,var(--border))}
.chip.busy{border:0;background:transparent;padding:0}
.spinner{width:12px;height:12px;border-radius:50%;border:2px solid var(--border-strong);border-top-color:var(--accent);animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.barbtn{border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);padding:6px 10px;font-size:12px;font-weight:500;cursor:pointer;display:flex;align-items:center;gap:6px}
.barbtn:hover{color:var(--text);border-color:var(--border-strong)}
.flowwrap{flex:1;position:relative;min-height:0}
.ctxback{position:fixed;inset:0;z-index:998}
.ctxmenu{position:fixed;z-index:999;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);box-shadow:0 6px 24px rgba(0,0,0,.4);padding:4px;min-width:170px}
.ctxmenu button{display:flex;align-items:center;gap:8px;width:100%;border:0;background:transparent;color:var(--text);font-size:13px;padding:8px 10px;border-radius:var(--radius);cursor:pointer;text-align:left}
.ctxmenu button:hover{background:var(--surface-3)}
.flowwrap :deep(.vue-flow__node){cursor:grab;border-radius:8px}
.flowwrap :deep(.vue-flow__controls){box-shadow:0 2px 10px rgba(0,0,0,.3);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.flowwrap :deep(.vue-flow__controls-button){background:var(--surface-2);border-bottom:1px solid var(--border);width:26px;height:26px;padding:6px}
.flowwrap :deep(.vue-flow__controls-button svg){fill:var(--text-dim)}
.flowwrap :deep(.vue-flow__controls-button:hover){background:var(--surface-3)}
.flowwrap :deep(.vue-flow__controls-button:hover svg){fill:var(--text)}
.flowwrap :deep(.vue-flow__node.selected){box-shadow:0 0 0 2px var(--accent)}
.flowwrap :deep(.vue-flow__selection){background:color-mix(in srgb,var(--accent) 14%,transparent);border:1px solid var(--accent);border-radius:4px}
.flowwrap :deep(.vue-flow__nodesselection-rect){background:color-mix(in srgb,var(--accent) 10%,transparent);border:1px solid var(--accent);border-radius:4px}

/* station (coupled Output + composition) */
.station{width:100%;height:100%;display:flex;flex-direction:column;border:1.5px solid var(--border-strong);border-radius:12px;overflow:hidden;background:color-mix(in srgb,var(--surface-1) 70%,transparent)}
.sthd{height:44px;flex-shrink:0;display:flex;align-items:center;gap:8px;padding:0 12px;border-bottom:1px solid var(--border);background:var(--surface-1)}
.sttitle{font-weight:600;font-size:13px}
.gzgen{border:0;border-radius:var(--radius);background:var(--accent);color:var(--on-accent);font-weight:600;font-size:12px;padding:6px 14px;cursor:pointer}
.gzgen:disabled{opacity:.5;cursor:default}
.stbody{flex:1;display:flex;min-height:0}
.stoutput{flex-basis:0;min-width:120px;display:flex;flex-direction:column;background:color-mix(in srgb,var(--surface-2) 40%,transparent)}
.colhd{height:26px;flex-shrink:0;display:flex;align-items:center;padding:0 12px;font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint);border-bottom:1px solid var(--border)}
.outbody{flex:1;position:relative}
.vsplit{width:6px;flex-shrink:0;cursor:col-resize;background:var(--border)}
.vsplit:hover{background:var(--accent)}
.hsplit{height:6px;flex-shrink:0;cursor:row-resize;background:var(--border)}
.hsplit:hover{background:var(--accent)}
.outhint{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;text-align:center;padding:20px;font-size:12px;color:var(--text-faint)}
.liveprev{position:absolute;inset:8px;width:calc(100% - 16px);height:calc(100% - 16px);object-fit:contain;border-radius:8px;
  border:1px solid var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 30%,transparent)}
.topwrap{position:absolute;inset:8px;display:flex;align-items:center;justify-content:center;cursor:grab}
.topwrap:active{cursor:grabbing}
.topimg{max-width:100%;max-height:100%;object-fit:contain;border-radius:8px;border:1px solid var(--border);box-shadow:0 2px 10px rgba(0,0,0,.35);transition:box-shadow .12s,border-color .12s}
.topwrap:hover .topimg{border-color:var(--border-strong)}
.topwrap.selected .topimg{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 45%,transparent)}
.stackbadge{position:absolute;top:2px;right:2px;font-size:10px;font-weight:600;background:color-mix(in srgb,#000 58%,transparent);color:#fff;padding:2px 8px;border-radius:20px}
.draghint{position:absolute;bottom:8px;left:50%;transform:translateX(-50%);font-size:10px;font-weight:600;background:var(--accent);color:var(--on-accent);padding:3px 10px;border-radius:20px;opacity:0;transition:opacity .12s;pointer-events:none;white-space:nowrap}
.topwrap:hover .draghint{opacity:1}
.stcomp{flex-basis:0;min-width:0;display:flex;flex-direction:column}
.lane{flex-basis:0;min-height:0;position:relative}
.lane.pos{background:color-mix(in srgb,var(--accent) 6%,transparent)}
.lane.neg{background:color-mix(in srgb,#e2483d 6%,transparent)}
.lanelbl{position:absolute;left:10px;top:8px;font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint)}
.stmeta{height:66px;flex-shrink:0;border-top:1px solid var(--border);background:var(--surface-1);padding:7px 12px;overflow:auto;font-size:11px;color:var(--text-dim)}
.stmeta .mrow{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stmeta .mrow b{color:var(--text-faint)}.stmeta .mrow.neg b{color:#e2483d}
.stmeta .mtok{margin-top:2px;color:var(--text-faint)}

.imgnode{position:relative;width:100%;height:100%;min-width:72px;min-height:104px;border-radius:8px;overflow:hidden;border:1px solid var(--border);background:var(--surface-2);box-shadow:0 2px 6px rgba(0,0,0,.25)}
.imgnode.selected,.imgnode.isnew{border-color:var(--accent)}
.imgnode.selected{box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 45%,transparent)}
.imgnode img{width:100%;height:100%;object-fit:cover;display:block}
.imgnode .new{position:absolute;left:5px;top:5px;font-size:9px;font-weight:700;background:var(--accent);color:#fff;padding:1px 6px;border-radius:10px;z-index:1}

.zonenode{width:100%;height:100%;border:1.5px dashed var(--border-strong);border-radius:12px;overflow:hidden;background:color-mix(in srgb,var(--surface-1) 55%,transparent)}
.zonenode.selected{border-color:var(--accent)}
.zonehd{display:flex;align-items:center;gap:8px;padding:8px 10px}
.zonename{font-size:13px;font-weight:600;color:var(--text);background:transparent;border:1px solid transparent;border-radius:var(--radius);padding:3px 6px;max-width:180px}
.zonename:focus{outline:none;border-color:var(--accent);background:var(--surface-2)}
.pill{font-size:10px;font-weight:600;color:var(--text-faint);border:1px solid var(--border-strong);padding:0 6px;border-radius:20px}
.zonemeta{display:flex;flex-direction:column;gap:6px;padding:2px 10px 10px}
.mini-in{font:inherit;font-size:12px;color:var(--text);background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:6px 8px;outline:none;resize:none}
.mini-in:focus{border-color:var(--accent)}

.block{width:100%;height:100%;min-height:34px;display:flex;flex-direction:column;border-radius:8px;border:1px solid var(--border);border-left:3px solid var(--cat);background:var(--surface-2);box-shadow:0 1px 4px rgba(0,0,0,.2);overflow:hidden}
.block.neg{border-left-color:#e2483d}
.bhd{flex-shrink:0;display:flex;align-items:center;gap:6px;padding:6px 8px}
.cdot{width:8px;height:8px;border-radius:50%;background:var(--cat);flex-shrink:0}
.block.neg .cdot{background:#e2483d}
.bname{flex:1;min-width:0;font:inherit;font-size:12px;font-weight:600;color:var(--text);background:transparent;border:1px solid transparent;border-radius:4px;padding:2px 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bname:focus{outline:none;border-color:var(--accent);background:var(--surface-1)}
.bname-text{cursor:default}
.bicon{width:20px;height:20px;flex-shrink:0;border:1px solid var(--border);background:var(--surface-1);color:var(--text-dim);border-radius:5px;font-size:11px;cursor:pointer;line-height:1}
.bicon:hover{color:var(--text);border-color:var(--border-strong)}
.bprev{padding:0 10px 8px;font-size:11px;color:var(--text-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.btext{flex:1;margin:0 8px 8px;width:calc(100% - 16px);min-height:52px;resize:none;font:inherit;font-size:11px;color:var(--text);background:var(--surface-1);border:1px solid var(--border);border-radius:5px;padding:6px;outline:none}
.btext:focus{border-color:var(--accent)}
</style>
