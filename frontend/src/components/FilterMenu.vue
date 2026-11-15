<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

// A filter dropdown that takes several values (design/global-gallery.html §2): the button says what is picked; the
// list — searchable when long — has a box per option and how many items it would list. No native <select>.
export interface FilterOption { value: string; label: string; count: number }
const props = defineProps<{ label: string; noun: string; options: FilterOption[]; selected: string[]; searchable?: boolean }>()
const emit = defineEmits<{ toggle: [string]; clear: [] }>()

const open = ref(false)
const find = ref('')
const root = ref<HTMLElement | null>(null)
const summary = computed(() => {
  if (!props.selected.length) return 'All'
  if (props.selected.length === 1) return props.options.find((o) => o.value === props.selected[0])?.label ?? props.selected[0]
  return `${props.selected.length} ${props.noun}s`
})
const shown = computed(() => {
  const q = find.value.trim().toLocaleLowerCase()
  return q ? props.options.filter((o) => o.label.toLocaleLowerCase().includes(q)) : props.options
})
// Closes on a press anywhere else — while it is open only.
function outside(e: PointerEvent) { if (!root.value?.contains(e.target as Node)) open.value = false }
watch(open, (o) => {
  if (o) document.addEventListener('pointerdown', outside, true)
  else { document.removeEventListener('pointerdown', outside, true); find.value = '' }
})
onBeforeUnmount(() => document.removeEventListener('pointerdown', outside, true))
</script>

<template>
  <div ref="root" class="fm">
    <button class="dd" :class="{ set: selected.length }" :aria-expanded="open" @click="open = !open">
      {{ label }}: <b>{{ summary }}</b> <span class="car">▾</span>
    </button>
    <div v-if="open" class="pop" @keydown.esc="open = false">
      <input v-if="searchable" v-model="find" class="pin" type="text" :placeholder="`Find a ${noun}…`" />
      <div class="rows">
        <button v-for="o in shown" :key="o.value" class="row" :class="{ on: selected.includes(o.value) }" @click="emit('toggle', o.value)">
          <span class="box">{{ selected.includes(o.value) ? '✓' : '' }}</span>
          <span class="lbl">{{ o.label }}</span><span class="n">{{ o.count }}</span>
        </button>
        <div v-if="!shown.length" class="none">{{ options.length ? `No ${noun} “${find.trim()}”.` : `No ${noun}s here.` }}</div>
      </div>
      <div v-if="selected.length" class="ft"><button class="linkbtn" @click="emit('clear')">Clear</button></div>
    </div>
  </div>
</template>

<style scoped>
.fm { position: relative; }
.dd { height: 32px; display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border-strong);
  background: var(--surface-2); color: var(--text-dim); border-radius: var(--radius); padding: 0 11px; font: inherit;
  font-size: 12.5px; font-weight: 600; white-space: nowrap; cursor: pointer; max-width: 260px; }
.dd b { color: var(--text); font-weight: 600; overflow: hidden; text-overflow: ellipsis; }
.dd:hover { color: var(--text); }
.dd.set { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); background: var(--nav-active); color: var(--accent); }
.dd.set b { color: var(--accent); }
.car { font-size: 10px; opacity: .8; }
.pop { position: absolute; left: 0; top: calc(100% + 5px); z-index: 30; width: 280px; padding: 6px; background: var(--surface-1);
  border: 1px solid var(--border-strong); border-radius: var(--radius-lg); box-shadow: 0 10px 30px rgba(0, 0, 0, .45); }
.pin { width: 100%; height: 30px; font: inherit; font-size: 12px; color: var(--text); background: var(--surface-2);
  border: 1px solid var(--border); border-radius: var(--radius); padding: 0 9px; outline: none; margin-bottom: 5px; }
.pin:focus { border-color: var(--accent); }
.rows { max-height: 280px; overflow-y: auto; }
.row { width: 100%; display: flex; align-items: center; gap: 8px; padding: 6px 8px; border: 0; border-radius: var(--radius);
  background: transparent; color: var(--text); font: inherit; font-size: 12.5px; text-align: left; cursor: pointer; }
.row:hover { background: var(--surface-3); }
.row.on { color: var(--accent); }
.lbl { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.n { font-size: 11px; color: var(--text-faint); font-variant-numeric: tabular-nums; }
.box { flex-shrink: 0; width: 16px; height: 16px; border-radius: 4px; border: 1.5px solid var(--border-strong);
  background: var(--surface-1); color: var(--on-accent); font-size: 10px; font-weight: 800;
  display: inline-flex; align-items: center; justify-content: center; }
.row.on .box { background: var(--accent); border-color: var(--accent); }
.none { font-size: 12px; color: var(--text-faint); padding: 6px 8px; }
.ft { display: flex; padding: 6px 4px 2px; border-top: 1px solid var(--border); margin-top: 5px; }
.linkbtn { border: 0; background: transparent; color: var(--accent); cursor: pointer; font: inherit; font-size: 12px;
  font-weight: 600; padding: 0 4px; }
</style>
