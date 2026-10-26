<script setup lang="ts">
/* The image viewer (design/ui-polish.html §1–3): pages through the set it was opened from (← → / wheel /
 * arrows / filmstrip) and offers that set's actions (useImagePreview). Sits under dialogs (z 1900 < 2000), so
 * an action's confirm opens on top.
 *
 * Modal for the keyboard: its listener runs in the capture phase and keeps keys from reaching the canvas
 * underneath (Delete there would remove selected nodes; arrows would nudge them). Keys typed into a field
 * (a dialog's input), app shortcuts (Ctrl/⌘ …) and keys while an action's dialog is up pass through. */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useImagePreview, type ViewerAction, type ViewerItem } from '../composables/useImagePreview'
import { downscaleDataUrl } from '../canvas/thumb'

const { set, currentId, close } = useImagePreview()

const items = computed<ViewerItem[]>(() => set.value?.items() ?? [])
const index = computed(() => items.value.findIndex((it) => it.id === currentId.value))
const item = computed(() => items.value[index.value] ?? null)
const actions = computed(() => (item.value ? (set.value?.actions ?? []).filter((a) => !a.enabled || a.enabled(item.value!)) : []))
const busy = ref(false)

// The current image left the set (kept / removed / deleted): show the one that took its place, or close.
let lastIndex = 0
watch(set, () => { lastIndex = 0 })
watch([items, index], () => {
  if (!set.value) return
  if (index.value >= 0) { lastIndex = index.value; return }
  if (!items.value.length) { close(); return }
  currentId.value = items.value[Math.min(lastIndex, items.value.length - 1)].id
})

function go(step: number) {
  const next = items.value[index.value + step]
  if (next) currentId.value = next.id
}
const labelOf = (a: ViewerAction) => (typeof a.label === 'function' && item.value ? a.label(item.value) : String(a.label))
async function run(a: ViewerAction) {
  const it = item.value
  if (!it || busy.value) return
  if (a.closes) close()
  busy.value = true
  try { await a.run(it) } finally { busy.value = false }
}

function onKey(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
  if (e.ctrlKey || e.metaKey || e.altKey) return
  if (busy.value) return // an action's dialog owns the keyboard
  e.stopPropagation()
  if (e.key === 'Escape') close()
  else if (e.key === 'ArrowLeft') go(-1)
  else if (e.key === 'ArrowRight') go(1)
  else {
    // Action keys fire once per press — holding Delete must not empty a stack of paid generations.
    const a = e.repeat ? undefined : actions.value.find((x) => x.key && x.key.toLowerCase() === e.key.toLowerCase())
    if (!a) return
    run(a)
  }
  e.preventDefault()
}
let wheelAt = 0
function onWheel(e: WheelEvent) {
  const now = performance.now()
  if (now - wheelAt < 140 || Math.abs(e.deltaY) < 4) return // one step per notch, not per pixel
  wheelAt = now
  go(e.deltaY > 0 ? 1 : -1)
}
// Take focus while open (a focused canvas node would otherwise keep reacting to keys) and give it back after.
const root = ref<HTMLElement | null>(null)
let returnFocus: HTMLElement | null = null
watch(set, (s, before) => {
  if (s && !before) {
    window.addEventListener('keydown', onKey, true)
    returnFocus = document.activeElement as HTMLElement | null
    nextTick(() => root.value?.focus())
  } else if (!s && before) {
    window.removeEventListener('keydown', onKey, true)
    returnFocus?.focus?.()
    returnFocus = null
  }
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey, true))

// Filmstrip: a window around the current image. Small sources only — a vault image asks the server for a
// thumbnail, a fresh data: URL is downscaled once (decoding dozens of full-size ones pixelates; ui-design.md).
const STRIP = 15
const STRIP_PX = 128 // the tiles' long side × 2 (HiDPI)
const strip = computed(() => {
  const start = Math.max(0, Math.min(index.value - Math.floor(STRIP / 2), items.value.length - STRIP))
  return items.value.slice(start, start + STRIP)
})
const thumbs = ref<Record<string, string>>({})
watch(strip, (list) => {
  for (const it of list) {
    if (it.url.startsWith('data:') && !(it.id in thumbs.value)) {
      thumbs.value[it.id] = '' // the tile stays empty until the small copy is ready — never the full source
      downscaleDataUrl(it.url, STRIP_PX).then((s) => { thumbs.value[it.id] = s }).catch(() => { /* stays empty */ })
    }
  }
})
watch(set, (s) => { if (!s) thumbs.value = {} })
const thumbSrc = (it: ViewerItem) => (it.url.startsWith('data:') ? thumbs.value[it.id] || '' : `${it.url}?w=${STRIP_PX}`)
</script>

<template>
  <Teleport to="body">
    <div v-if="set && item" ref="root" class="viewer" tabindex="-1" @click="close" @contextmenu.prevent @wheel.prevent="onWheel">
      <div class="vtop" @click.stop>
        <span class="vsrc">{{ set.source }}</span>
        <span v-if="items.length > 1" class="vcnt">{{ index + 1 }} / {{ items.length }}</span>
        <span v-if="item.favorite" class="vfav" title="Favourite">★</span>
        <span v-if="item.cover" class="vcover" title="The work's cover — its picture in the Works list">Cover</span>
        <span v-if="item.meta" class="vmeta">{{ item.meta }}</span>
        <span class="sp"></span>
        <button class="vx" title="Close (Esc)" @click="close">✕</button>
      </div>

      <div class="vstage">
        <button v-if="items.length > 1" class="varr" :disabled="index <= 0" title="Previous (←)" @click.stop="go(-1)">‹</button>
        <img class="vimg" :src="item.url" alt="preview" draggable="false" @click.stop />
        <button v-if="items.length > 1" class="varr" :disabled="index >= items.length - 1" title="Next (→)" @click.stop="go(1)">›</button>
      </div>

      <div v-if="actions.length" class="vacts" @click.stop>
        <button v-for="(a, i) in actions" :key="i" class="va" :class="{ pri: a.primary, del: a.danger }" :disabled="busy" @click="run(a)">
          {{ a.icon }} {{ labelOf(a) }}<kbd v-if="a.key">{{ a.key === 'Delete' ? 'Del' : a.key.toUpperCase() }}</kbd>
        </button>
      </div>

      <div v-if="items.length > 1" class="vstrip" @click.stop>
        <button v-for="it in strip" :key="it.id" class="vth" :class="{ on: it.id === item.id }" @click="currentId = it.id">
          <img v-if="thumbSrc(it)" :src="thumbSrc(it)" alt="" draggable="false" />
        </button>
      </div>
      <div class="vhint"><kbd>←</kbd> <kbd>→</kbd> / wheel · <kbd>Esc</kbd> close</div>
    </div>
  </Teleport>
</template>

<style scoped>
/* The viewer is always dark (a lightbox), whatever the theme — fixed greys, not theme tokens. */
.viewer{position:fixed;inset:0;z-index:1900;background:rgba(8,9,11,.94);display:flex;flex-direction:column;cursor:zoom-out;color:#c7d1db;outline:none}
.vtop{display:flex;align-items:center;gap:10px;padding:12px 16px;font-size:12.5px;cursor:default}
.vsrc{font-size:10.5px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:#9fadbc;background:rgba(255,255,255,.07);
  border:1px solid rgba(255,255,255,.12);border-radius:10px;padding:1px 8px}
.vcnt{font-weight:700;font-variant-numeric:tabular-nums}
.vfav{color:var(--star)}
.vcover{font-size:10.5px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:#fff;background:rgba(255,255,255,.14);
  border:1px solid rgba(255,255,255,.32);border-radius:10px;padding:1px 8px}
.vmeta{color:#738496;font-size:11.5px}
.sp{flex:1}
.vx{width:34px;height:34px;border-radius:50%;border:1px solid #454f59;background:#1d2125;color:#9fadbc;font-size:14px;cursor:pointer;
  display:flex;align-items:center;justify-content:center}
.vx:hover{color:#fff}
.vstage{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;gap:26px;padding:0 20px}
.vimg{max-width:calc(100% - 140px);max-height:100%;object-fit:contain;border-radius:6px;box-shadow:0 18px 60px rgba(0,0,0,.6);cursor:default}
.varr{flex-shrink:0;width:42px;height:42px;border-radius:50%;border:1px solid #454f59;background:rgba(29,33,37,.85);color:#c7d1db;
  font-size:20px;line-height:1;cursor:pointer;display:flex;align-items:center;justify-content:center}
.varr:hover:not(:disabled){color:#fff;border-color:#738496}
.varr:disabled{opacity:.25;cursor:default}
.vacts{display:flex;justify-content:center;gap:8px;padding:12px 0 8px;cursor:default}
.va{display:inline-flex;align-items:center;gap:7px;border:1px solid #454f59;background:#1d2125;color:#c7d1db;border-radius:7px;
  padding:7px 13px;font-size:12.5px;font-weight:600;cursor:pointer}
.va:hover:not(:disabled){border-color:#738496;color:#fff}
.va.pri{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.va.pri:hover:not(:disabled){background:var(--accent-strong)}
.va.del{color:#f08a80}
.va:disabled{opacity:.5;cursor:default}
.va kbd{font:inherit;font-size:10px;color:#9fadbc;border:1px solid #454f59;border-radius:4px;padding:0 4px}
.va.pri kbd{color:inherit;border-color:rgba(255,255,255,.4)}
.vstrip{display:flex;gap:6px;justify-content:center;padding:4px 16px 14px;cursor:default}
.vth{width:40px;height:56px;padding:0;border-radius:4px;border:1.5px solid transparent;background:#1d2125;opacity:.55;cursor:pointer;overflow:hidden}
.vth img{width:100%;height:100%;object-fit:cover;display:block}
.vth:hover{opacity:.85}
.vth.on{opacity:1;border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 40%,transparent)}
.vhint{position:fixed;bottom:12px;right:16px;font-size:10.5px;color:#738496;pointer-events:none}
.vhint kbd{font:inherit;border:1px solid #454f59;border-radius:4px;padding:0 4px;color:#9fadbc}
</style>
