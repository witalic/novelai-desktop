<script setup lang="ts">
import { computed, ref } from 'vue'
import type { TagCount } from '../types'

// The tag filter under a toolbar (design/works-tags.html, A) — TagToggle opens it. Open: the tags on offer, most used
// first and the picked ones ahead, each with how many items it would list, a box to find one, three rows at most (the
// rest scroll). Closed with tags picked: just those, each with ✕. Picking several narrows to what has them all.
const props = defineProps<{ tags: TagCount[]; selected: string[]; open: boolean }>()
const emit = defineEmits<{ toggle: [string]; clear: [] }>()

const find = ref('')
const shown = computed(() => {
  const q = find.value.trim().toLocaleLowerCase()
  const picked = props.tags.filter((t) => props.selected.includes(t.name))
  const rest = props.tags.filter((t) => !props.selected.includes(t.name) && (!q || t.name.toLocaleLowerCase().includes(q)))
  return [...picked, ...rest]
})
</script>

<template>
  <div v-show="open || selected.length > 0" class="tagfilter">
    <div v-show="open" class="panel">
      <div class="phd">
        <span class="findw"><span class="ic">⌕</span><input v-model="find" class="find" type="text" placeholder="Find a tag…" /></span>
        <span class="hint">Most used first · several picked = what has them all</span>
        <span class="sp"></span>
        <button v-if="selected.length" class="linkbtn" @click="emit('clear')">Clear</button>
      </div>
      <div v-if="shown.length" class="chips">
        <button v-for="t in shown" :key="t.name" class="chip" :class="{ on: selected.includes(t.name) }"
          @click="emit('toggle', t.name)">
          {{ t.name }} <span class="n">{{ t.count }}</span>
        </button>
      </div>
      <div v-else class="none">{{ find.trim() ? `No tag “${find.trim()}” here.` : 'No tags yet.' }}</div>
    </div>
    <div v-if="!open && selected.length" class="picked">
      <button v-for="t in selected" :key="t" class="chip on" :title="`Stop filtering by “${t}”`" @click="emit('toggle', t)">
        {{ t }} <span class="x">✕</span>
      </button>
      <button class="linkbtn" @click="emit('clear')">Clear</button>
    </div>
  </div>
</template>

<style scoped>
.panel { border: 1px solid var(--border); background: var(--surface-1); border-radius: var(--radius-lg);
  padding: 10px 12px 12px; }
.phd { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.findw { position: relative; }
.findw .ic { position: absolute; left: 9px; top: 50%; transform: translateY(-50%); color: var(--text-faint); font-size: 12px; pointer-events: none; }
.find { height: 28px; width: 220px; font: inherit; font-size: 12px; color: var(--text); background: var(--surface-2);
  border: 1px solid var(--border); border-radius: var(--radius); padding: 0 9px 0 26px; outline: none; }
.find:focus { border-color: var(--accent); }
.hint { font-size: 11.5px; color: var(--text-faint); }
.sp { flex: 1; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; max-height: 84px; overflow-y: auto; } /* three rows, the rest scroll */
.none { font-size: 12px; color: var(--text-faint); }
.picked { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.chip { height: 24px; display: inline-flex; align-items: center; gap: 6px; border: 0; background: var(--surface-3);
  color: var(--text-dim); border-radius: 5px; padding: 0 9px; font: inherit; font-size: 12px; font-weight: 550;
  white-space: nowrap; cursor: pointer; }
.chip:hover { background: color-mix(in srgb, var(--text) 10%, var(--surface-3)); color: var(--text); }
.chip .n { color: var(--text-faint); font-size: 10.5px; font-weight: 500; font-variant-numeric: tabular-nums; }
.chip.on { background: var(--accent); color: var(--on-accent); }
.chip.on:hover { background: var(--accent-strong); }
.chip.on .n { color: color-mix(in srgb, var(--on-accent) 75%, transparent); }
.chip .x { font-size: 10px; opacity: .85; }
.linkbtn { border: 0; background: transparent; color: var(--accent); cursor: pointer; font: inherit; font-size: 12px;
  font-weight: 600; padding: 0 4px; }
</style>
