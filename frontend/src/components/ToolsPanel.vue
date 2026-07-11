<script setup lang="ts">
/* The right-hand "Tools" panel (design/presets-mockup.html): three tabs so presets, params, the
 * generated stack, and a widget palette each get room. Generation holds the Preset strip + params;
 * Stack manages the generated pile (drag a thumbnail onto the canvas to keep — same as the station
 * Output slot); Widgets is a draggable palette (informational for now). */
import { computed, ref, watch } from 'vue'
import Dropdown from './Dropdown.vue'
import ParamFields from './ParamFields.vue'
import { presetParamsDiffer } from '../presets/diff'
import { anlasCost } from '../presets/cost'
import { useAccount } from '../composables/useAccount'
import { useContextMenu } from '../composables/useContextMenu'
import { useImagePreview } from '../composables/useImagePreview'
import { downscaleDataUrl } from '../canvas/thumb'
import type { GenResult, PanelParams, Preset } from '../types'

const props = defineProps<{
  params: PanelParams
  open: boolean
  drafts: GenResult[]
  busy: boolean
  presets: Preset[]
  activePresetId: string | null
}>()
const emit = defineEmits<{
  toggle: []
  'pick-preset': [Preset]
  'update-preset': []
  'save-as': []
  'clear-stack': []
  'keep-many': [string[]] // materialise these drafts onto the canvas, in order
  'remove-many': [string[]] // drop these drafts from the stack
  navigate: [string]
}>()

// Stack thumbnails, same crispness lesson as the canvas (rules/ui-design.md): the <img> is laid
// out at ~2× the box (`.timg` width/height:200%) so it DECODES at high resolution, then a
// transform shrinks it to the box — a full-res data: URL is no longer single-step-crushed into
// ~150px (which pixelates on HiDPI). Vault drafts additionally fetch a `?w=` server thumbnail so
// the source is already modest.
const dpr = Math.min(Math.max(window.devicePixelRatio || 1, 1), 3)
const THUMB_W = Math.round(180 * 2 * dpr) // must be ≥ the 200% decode size so vault images don't upscale

// Fresh generations are full-resolution data: URLs; many of them at once make Chromium downsample
// decoded bitmaps (→ pixelation). Downscale each once (client-side) and render the small copy, so the
// stack stays crisp like the vault path. Cache by draft id; prune when a draft leaves the stack.
const thumbCache = ref<Record<string, string>>({})
watch(() => props.drafts, (ds) => {
  for (const d of ds) {
    if (d.url?.startsWith('data:') && !thumbCache.value[d.id]) {
      downscaleDataUrl(d.url, THUMB_W).then((s) => { thumbCache.value[d.id] = s }).catch(() => { /* keep source */ })
    }
  }
  const live = new Set(ds.map((d) => d.id))
  for (const id of Object.keys(thumbCache.value)) if (!live.has(id)) delete thumbCache.value[id]
}, { immediate: true })

function thumbSrc(d: GenResult): string {
  if (!d.url) return ''
  if (!d.url.startsWith('data:')) return `${d.url}?w=${THUMB_W}` // vault image → server-sized thumbnail
  return thumbCache.value[d.id] || d.url // downscaled copy once ready; full source until then
}

// Right-click a stack thumbnail → preview (full res) / keep on canvas / remove from the stack.
const { open: openMenu } = useContextMenu()
const { preview } = useImagePreview()
function onThumbMenu(e: MouseEvent, d: GenResult) {
  openMenu(e, [
    { label: 'Preview', icon: '⤢', onClick: () => preview(d.url) },
    { label: 'Move to canvas', icon: '⤒', onClick: () => emit('keep-many', [d.id]) },
    { label: 'Remove from stack', icon: '🗑', danger: true, onClick: () => emit('remove-many', [d.id]) },
  ])
}

type Tab = 'generation' | 'stack' | 'widgets'
const tab = ref<Tab>((sessionStorage.getItem('nai-tools-tab') as Tab) || 'generation')
watch(tab, (t) => sessionStorage.setItem('nai-tools-tab', t))

// ---- cost estimate: tier-aware (Opus gets the first sample free — see cost.ts), reflects the live params ----
const { subscription } = useAccount()
const genCost = computed(() => anlasCost(props.params, subscription.value?.tier ?? 0, !!subscription.value?.active))

// ---- preset strip ----
const activePreset = computed(() => props.presets.find((p) => p.id === props.activePresetId) ?? null)
const modified = computed(() => !!activePreset.value && presetParamsDiffer(props.params, activePreset.value.params))
const presetOptions = computed(() => props.presets.map((p) => ({
  value: p.id, label: p.name, group: p.builtin ? 'Built-in' : 'Your presets',
})))
function pick(id: string | number) {
  const p = props.presets.find((x) => x.id === id)
  if (p) emit('pick-preset', p)
}

// ---- widgets palette (informational) ----
const WIDGETS = [
  { icon: '✦', name: 'Prompt blocks', desc: 'search + pin prompt blocks', placed: true },
  { icon: '▤', name: 'Gallery', desc: 'kept images for this work', placed: true },
  { icon: '✎', name: 'Notes', desc: 'freeform text on the canvas', soon: true },
  { icon: '◨', name: 'Reference', desc: 'img2img / reference source', soon: true },
]

// Drag a thumbnail onto the canvas to keep it. If the dragged one is part of a multi-selection,
// the whole selected group travels (in stack order); otherwise just that one.
function onThumbDragStart(e: DragEvent, d: GenResult) {
  if (!e.dataTransfer) return
  e.dataTransfer.effectAllowed = 'move'
  if (selected.value.has(d.id) && selected.value.size > 1) {
    const ids = props.drafts.filter((x) => selected.value.has(x.id)).map((x) => x.id)
    e.dataTransfer.setData('text/plain', `nai-drafts:${ids.join(',')}`)
  } else {
    e.dataTransfer.setData('text/plain', `nai-draft:${d.id}`)
  }
}

// ---- stack multi-select ----
const selected = ref<Set<string>>(new Set())
function toggleSelect(id: string) {
  const next = new Set(selected.value)
  next.has(id) ? next.delete(id) : next.add(id)
  selected.value = next
}
const allSelected = computed(() => props.drafts.length > 0 && selected.value.size === props.drafts.length)
function toggleSelectAll() {
  selected.value = allSelected.value ? new Set() : new Set(props.drafts.map((d) => d.id))
}
// Keep in stack order (props.drafts is newest-first); remove the kept ones from the stack after.
function keepSelected() {
  const ids = props.drafts.filter((d) => selected.value.has(d.id)).map((d) => d.id)
  if (ids.length) emit('keep-many', ids)
  selected.value = new Set()
}
function removeSelected() {
  if (selected.value.size) emit('remove-many', [...selected.value])
  selected.value = new Set()
}
// Prune the selection when the stack changes underneath it (kept/cleared elsewhere).
watch(() => props.drafts, (ds) => {
  const live = new Set(ds.map((d) => d.id))
  if ([...selected.value].some((id) => !live.has(id))) selected.value = new Set([...selected.value].filter((id) => live.has(id)))
})
</script>

<template>
  <aside class="tools" :class="{ closed: !open }">
    <button v-if="!open" class="rail" title="Show tools" @click="emit('toggle')"><span class="railicon">⚙</span></button>

    <template v-else>
      <div class="hd"><span>Tools</span><button class="collapse" title="Hide" @click="emit('toggle')">›</button></div>

      <div class="tabbar">
        <button :class="{ active: tab === 'generation' }" @click="tab = 'generation'">Generation</button>
        <button :class="{ active: tab === 'stack' }" @click="tab = 'stack'">Stack <span v-if="drafts.length" class="tn">{{ drafts.length }}</span></button>
        <button :class="{ active: tab === 'widgets' }" @click="tab = 'widgets'">Widgets</button>
      </div>

      <!-- ===== Generation ===== -->
      <div v-if="tab === 'generation'" class="body">
        <div class="pstrip">
          <div class="pstop">
            <span class="pslbl">Preset</span>
            <span v-if="modified" class="modtag" title="Params differ from the active preset">Modified</span>
            <div class="sp"></div>
            <button class="plink" @click="emit('save-as')">Save as…</button>
            <button v-if="activePreset && !activePreset.builtin && modified" class="plink" @click="emit('update-preset')">Update</button>
          </div>
          <Dropdown :model-value="activePresetId ?? ''" :options="presetOptions" @update:model-value="pick" />
        </div>
        <ParamFields :params="params" />
        <div class="costcard">
          <div class="crow"><span class="clbl">Estimated cost</span>
            <span class="cval">{{ genCost === 0 ? 'Free' : `◆ ${genCost}` }}</span></div>
          <div v-if="subscription" class="crow"><span class="clbl">Balance</span>
            <span class="cbal"><span class="ctier">{{ subscription.tier_name }}</span>◆ {{ subscription.anlas.toLocaleString() }}</span></div>
        </div>
      </div>

      <!-- ===== Stack ===== -->
      <div v-else-if="tab === 'stack'" class="body stack">
        <div class="stackintro">The <b>stack</b> holds every image you generate — newest first, kept until you keep it on the canvas or clear it.</div>
        <div v-if="!drafts.length" class="empty">No generations yet.<br />Hit Generate — results pile up here.</div>
        <template v-else>
          <div class="stackbar">
            <span class="cnt">{{ drafts.length }} generated</span>
            <button class="stacklink" @click="toggleSelectAll">{{ allSelected ? 'Deselect all' : 'Select all' }}</button>
            <div class="sp"></div>
            <button class="stackbtn danger" @click="emit('clear-stack')">Clear all</button>
          </div>

          <div v-if="selected.size" class="selbar">
            <span class="selcnt">{{ selected.size }} selected</span>
            <div class="sp"></div>
            <button class="stackbtn" @click="keepSelected">⤒ Keep on canvas</button>
            <button class="stackbtn danger" @click="removeSelected">Remove</button>
          </div>
          <div v-else class="hint">Click to select · drag a thumbnail onto the canvas to keep just that one.</div>

          <div class="thumbs">
            <div v-for="(d, i) in drafts" :key="d.id" class="thumb" :class="{ top: i === 0, on: selected.has(d.id) }"
              draggable="true" @dragstart="onThumbDragStart($event, d)" @click="toggleSelect(d.id)" @contextmenu="onThumbMenu($event, d)">
              <span class="check">{{ selected.has(d.id) ? '✓' : '' }}</span>
              <span v-if="i === 0" class="topbadge">TOP</span>
              <span v-if="d.mock" class="mockbadge">MOCK</span>
              <img class="timg" :src="thumbSrc(d)" alt="generation" draggable="false" />
            </div>
          </div>
        </template>
      </div>

      <!-- ===== Widgets ===== -->
      <div v-else class="body">
        <div class="hint">Drag a widget onto the canvas to add it. Placed ones are greyed.</div>
        <div class="wtiles">
          <div v-for="w in WIDGETS" :key="w.name" class="wtile" :class="{ placed: w.placed, soon: w.soon }">
            <span class="wi">{{ w.icon }}</span>
            <div><div class="wn">{{ w.name }}</div><div class="wd">{{ w.placed ? 'on canvas' : w.desc }}</div></div>
            <span v-if="w.soon" class="soonbadge">soon</span>
            <span v-else class="wgrip">⠿</span>
          </div>
        </div>
        <div class="whint">Notes and Reference widgets are coming; Prompt blocks and Gallery are always on the canvas.</div>
      </div>
    </template>
  </aside>
</template>

<style scoped>
.tools{background:var(--surface-1);border-left:1px solid var(--border);display:flex;flex-direction:column;min-height:0;order:1}
.rail{width:100%;height:100%;border:0;background:transparent;color:var(--text-dim);cursor:pointer;display:flex;justify-content:center;padding-top:16px}
.rail:hover{color:var(--text)}.railicon{font-size:18px}
.hd{display:flex;align-items:center;justify-content:space-between;padding:15px 18px;border-bottom:1px solid var(--border);font-weight:600;font-size:15px;flex-shrink:0}
.collapse{border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);width:26px;height:26px;cursor:pointer}
.collapse:hover{color:var(--text);border-color:var(--border-strong)}

.tabbar{display:flex;flex-shrink:0;border-bottom:1px solid var(--border)}
.tabbar button{flex:1;border:0;border-bottom:2px solid transparent;background:transparent;color:var(--text-dim);
  font:inherit;font-size:12.5px;font-weight:600;padding:10px 4px;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px}
.tabbar button:hover{color:var(--text)}
.tabbar button.active{color:var(--accent);border-bottom-color:var(--accent)}
.tabbar .tn{font-size:10px;font-weight:700;color:var(--text-faint);background:var(--surface-3);border-radius:10px;padding:0 6px}
.tabbar button.active .tn{color:var(--accent)}

.body{flex:1;overflow-y:auto;padding:16px 18px;display:flex;flex-direction:column;gap:15px}

.pstrip{border:1px solid var(--border);border-radius:var(--radius-lg);background:var(--surface-2);padding:10px 12px;display:flex;flex-direction:column;gap:8px}
.pstop{display:flex;align-items:center;gap:8px}
.pstop .sp{flex:1}
.pslbl{font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint)}
.modtag{font-size:9px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;color:var(--warn);
  border:1px solid color-mix(in srgb,var(--warn) 45%,var(--border));border-radius:10px;padding:0 6px}
.plink{border:0;background:transparent;color:var(--accent);font:inherit;font-size:12px;font-weight:600;cursor:pointer;padding:0}

.costcard{border:1px solid var(--border);border-radius:var(--radius-lg);background:var(--surface-2);padding:11px 12px;display:flex;flex-direction:column;gap:7px}
.costcard .crow{display:flex;align-items:center;justify-content:space-between}
.costcard .clbl{font-size:11.5px;color:var(--text-dim)}
.costcard .cval{font-size:13px;font-weight:700;color:var(--accent);font-variant-numeric:tabular-nums}
.costcard .cbal{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:700;color:var(--text-dim);font-variant-numeric:tabular-nums}
.costcard .ctier{font-size:9.5px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;color:var(--accent);
  background:var(--nav-active);border:1px solid color-mix(in srgb,var(--accent) 35%,var(--border));border-radius:10px;padding:1px 6px}

.stackintro{font-size:11.5px;color:var(--text-faint);line-height:1.5}
.stackintro b{color:var(--text-dim)}
.stack .empty{margin:auto;text-align:center;font-size:12.5px;color:var(--text-faint)}
.stackbar{display:flex;align-items:center;gap:10px;font-size:11px;color:var(--text-faint)}
.stackbar .cnt{font-weight:600;color:var(--text-dim)}
.stackbar .sp{flex:1}
.stacklink{border:0;background:transparent;color:var(--accent);font:inherit;font-size:11px;font-weight:600;cursor:pointer;padding:0}
.stackbtn{border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);font:inherit;font-size:11px;font-weight:600;padding:4px 10px;cursor:pointer}
.stackbtn:hover{color:var(--text);border-color:var(--border-strong)}
.stackbtn.danger:hover{color:var(--danger);border-color:var(--danger)}
.selbar{display:flex;align-items:center;gap:8px;padding:7px 10px;border:1px solid color-mix(in srgb,var(--accent) 40%,var(--border));
  border-radius:var(--radius);background:color-mix(in srgb,var(--accent) 8%,var(--surface-2))}
.selbar .selcnt{font-size:11px;font-weight:600;color:var(--accent)}
.selbar .sp{flex:1}
.hint{font-size:10.5px;color:var(--text-faint)}
.thumbs{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.thumb{position:relative;aspect-ratio:2/3;border-radius:8px;border:1px solid var(--border);overflow:hidden;cursor:pointer;background:var(--surface-2)}
.thumb.on{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 45%,transparent)}
/* Decode at 2× the box, then transform back down — crisp on HiDPI for both data: and vault srcs. */
.timg{position:absolute;top:50%;left:50%;width:200%;height:200%;object-fit:cover;display:block;
  transform:translate(-50%,-50%) scale(.5);transform-origin:center}
.thumb .check{position:absolute;top:5px;left:5px;z-index:2;width:17px;height:17px;border-radius:50%;
  border:1.5px solid #fff;background:color-mix(in srgb,#000 45%,transparent);color:#fff;font-size:10px;font-weight:800;
  display:flex;align-items:center;justify-content:center}
.thumb.on .check{background:var(--accent);border-color:var(--accent)}
.topbadge{position:absolute;bottom:5px;left:5px;z-index:1;font-size:8.5px;font-weight:800;background:var(--accent);color:#fff;padding:1px 6px;border-radius:8px}
.mockbadge{position:absolute;top:5px;right:5px;z-index:1;font-size:8px;font-weight:800;background:var(--warn);color:#fff;padding:1px 5px;border-radius:8px}

.wtiles{display:flex;flex-direction:column;gap:8px}
.wtile{display:flex;align-items:center;gap:11px;border:1px solid var(--border);border-radius:var(--radius-lg);background:var(--surface-2);padding:11px 12px;cursor:grab}
.wtile.placed,.wtile.soon{opacity:.5;cursor:default}
.wtile .wi{width:30px;height:30px;flex-shrink:0;border-radius:7px;background:var(--surface-3);display:flex;align-items:center;justify-content:center;font-size:15px;color:var(--accent)}
.wtile .wn{font-size:12.5px;font-weight:600}
.wtile .wd{font-size:10.5px;color:var(--text-faint);margin-top:1px}
.wtile .wgrip{margin-left:auto;color:var(--text-faint);font-size:12px}
.soonbadge{margin-left:auto;font-size:9px;font-weight:700;color:var(--text-faint);border:1px solid var(--border-strong);border-radius:20px;padding:0 7px}
.whint{font-size:10.5px;color:var(--text-faint)}
</style>
