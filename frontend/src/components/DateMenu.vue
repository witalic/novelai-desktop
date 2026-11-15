<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { dayOf, dayStart, periodLabel, UI_LOCALE, type Period } from '../gallery/query'

// The Date filter (design/global-gallery.html §2): a period, or a range picked on a calendar with two clicks — in the
// viewer's own days. No native date picker.
const props = defineProps<{ period: Period }>()
const emit = defineEmits<{ pick: [Period] }>()

const PRESETS: Period[] = [{ kind: 'any' }, { kind: 'today' }, { kind: 'week' }, { kind: 'month' }]
const open = ref(false)
const root = ref<HTMLElement | null>(null)
const today = () => dayOf(new Date())
const month = ref(new Date()) // the month the calendar shows (its first day matters)
const anchor = ref<string | null>(null) // the range's first click, waiting for its second

function outside(e: PointerEvent) { if (!root.value?.contains(e.target as Node)) open.value = false }
watch(open, (o) => {
  if (o) {
    document.addEventListener('pointerdown', outside, true)
    month.value = props.period.kind === 'range' ? dayStart(props.period.from) : new Date()
  } else {
    document.removeEventListener('pointerdown', outside, true)
    anchor.value = null
  }
})
onBeforeUnmount(() => document.removeEventListener('pointerdown', outside, true))

function pick(p: Period) { emit('pick', p); open.value = false }
function step(n: number) { month.value = new Date(month.value.getFullYear(), month.value.getMonth() + n, 1) }
function clickDay(day: string) {
  if (!anchor.value) { anchor.value = day; return }
  const [from, to] = [anchor.value, day].sort()
  pick({ kind: 'range', from, to })
}

const WEEKDAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']
// Six weeks from the Monday on or before the 1st — the days of the next and last month greyed.
const days = computed(() => {
  const first = new Date(month.value.getFullYear(), month.value.getMonth(), 1)
  const start = new Date(first.getFullYear(), first.getMonth(), 1 - ((first.getDay() + 6) % 7))
  return Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i)
    return { day: dayOf(d), n: d.getDate(), other: d.getMonth() !== first.getMonth() }
  })
})
const range = computed(() => {
  if (anchor.value) return { from: anchor.value, to: anchor.value }
  return props.period.kind === 'range' ? props.period : null
})
const monthLabel = computed(() => month.value.toLocaleDateString(UI_LOCALE, { month: 'long', year: 'numeric' }))
</script>

<template>
  <div ref="root" class="dm">
    <button class="dd" :class="{ set: period.kind !== 'any' }" :aria-expanded="open" @click="open = !open">
      Date: <b>{{ periodLabel(period) }}</b> <span class="car">▾</span>
    </button>
    <div v-if="open" class="pop" @keydown.esc="open = false">
      <button v-for="p in PRESETS" :key="p.kind" class="row" :class="{ on: period.kind === p.kind }" @click="pick(p)">
        {{ periodLabel(p) }}
      </button>
      <div class="sep"></div>
      <div class="hint">{{ anchor ? 'Now the other end of the range' : 'Custom range — click its first and last day' }}</div>
      <div class="calhd">
        <button class="nav" title="Previous month" @click="step(-1)">‹</button>
        <span>{{ monthLabel }}</span>
        <button class="nav" title="Next month" @click="step(1)">›</button>
      </div>
      <div class="cal">
        <span v-for="w in WEEKDAYS" :key="w" class="h">{{ w }}</span>
        <button v-for="d in days" :key="d.day" class="d"
          :class="{ m: d.other, now: d.day === today(), end: range && (d.day === range.from || d.day === range.to),
                    in: range && d.day > range.from && d.day < range.to }"
          :disabled="d.day > today()" @click="clickDay(d.day)">{{ d.n }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dm { position: relative; }
.dd { height: 32px; display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border-strong);
  background: var(--surface-2); color: var(--text-dim); border-radius: var(--radius); padding: 0 11px; font: inherit;
  font-size: 12.5px; font-weight: 600; white-space: nowrap; cursor: pointer; }
.dd b { color: var(--text); font-weight: 600; }
.dd:hover { color: var(--text); }
.dd.set { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); background: var(--nav-active); color: var(--accent); }
.dd.set b { color: var(--accent); }
.car { font-size: 10px; opacity: .8; }
.pop { position: absolute; left: 0; top: calc(100% + 5px); z-index: 30; width: 250px; padding: 6px; background: var(--surface-1);
  border: 1px solid var(--border-strong); border-radius: var(--radius-lg); box-shadow: 0 10px 30px rgba(0, 0, 0, .45); }
.row { width: 100%; display: block; padding: 6px 8px; border: 0; border-radius: var(--radius); background: transparent;
  color: var(--text); font: inherit; font-size: 12.5px; text-align: left; cursor: pointer; }
.row:hover { background: var(--surface-3); }
.row.on { color: var(--accent); }
.sep { height: 1px; background: var(--border); margin: 5px 2px; }
.hint { font-size: 11px; color: var(--text-faint); padding: 2px 8px 4px; }
.calhd { display: flex; align-items: center; justify-content: space-between; padding: 2px 4px; font-size: 12px; font-weight: 700; }
.nav { border: 0; background: transparent; color: var(--text-faint); font-size: 15px; padding: 0 8px; cursor: pointer; border-radius: var(--radius); }
.nav:hover { color: var(--text); background: var(--surface-3); }
.cal { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; padding: 4px; }
.h { text-align: center; font-size: 10px; font-weight: 700; color: var(--text-faint); padding: 3px 0; }
.d { padding: 4px 0; border: 0; border-radius: 4px; background: transparent; color: var(--text-dim); font: inherit;
  font-size: 11.5px; cursor: pointer; font-variant-numeric: tabular-nums; }
.d:hover:not(:disabled) { background: var(--surface-3); color: var(--text); }
.d.m { opacity: .45; }
.d.now { color: var(--text); font-weight: 700; }
.d.in { background: var(--nav-active); color: var(--accent); border-radius: 0; }
.d.end { background: var(--accent); color: var(--on-accent); }
.d:disabled { opacity: .25; cursor: default; }
</style>
