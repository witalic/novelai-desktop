<script setup lang="ts">
/* Dialogs of the Combinations feature (design/combinations-widget.html → States): "Save combination" (a
 * name + the blocks it will hold + keep-frozen) and "Replace the composition?" (shown only when Replace would
 * drop station blocks the combination doesn't have; offers to save them first). App-styled, teleported. */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { PersistedComboBlock } from '../types'

const props = defineProps<{
  mode: 'save' | 'replace'
  title: string
  // save
  name?: string
  blocks?: PersistedComboBlock[]
  catColor?: (slug: string) => string
  // replace
  message?: string
}>()
const emit = defineEmits<{
  save: [{ name: string; keepFrozen: boolean }]
  replace: [{ saveFirst: boolean }]
  close: []
}>()

const nameText = ref(props.name ?? '')
const keepFrozen = ref(true)
const saveFirst = ref(true)
const hasFrozen = computed(() => (props.blocks ?? []).some((b) => b.frozen))
const color = (slug?: string | null) => props.catColor?.(slug || 'custom') ?? 'var(--text-faint)'

const nameEl = ref<HTMLInputElement | null>(null)
onMounted(() => requestAnimationFrame(() => { nameEl.value?.focus(); nameEl.value?.select() }))
// Esc closes in both modes — Replace has no focused field, so a listener on the dialog would never hear it.
function onKey(e: KeyboardEvent) { if (e.key === 'Escape') emit('close') }
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))

function submit() {
  if (props.mode === 'save') {
    if (!nameText.value.trim()) return
    emit('save', { name: nameText.value.trim(), keepFrozen: keepFrozen.value })
  } else {
    emit('replace', { saveFirst: saveFirst.value })
  }
}
</script>

<template>
  <Teleport to="body">
    <div class="cd-back" @click="emit('close')" @contextmenu.prevent>
      <div class="cd-card" @click.stop>
        <div class="cd-hd">{{ title }}</div>
        <div class="cd-body">
          <template v-if="mode === 'save'">
            <label class="cd-lbl" for="cd-name">Name</label>
            <input id="cd-name" ref="nameEl" v-model="nameText" type="text" class="cd-in" @keydown.enter="submit" />
            <div class="cd-lbl">Blocks ({{ blocks?.length ?? 0 }}, in station order)</div>
            <div class="cd-chips">
              <span v-for="(b, i) in blocks" :key="i" class="cd-chip" :class="{ neg: b.polarity === 'negative' }">
                <i :style="{ background: color(b.category) }"></i>{{ b.name || b.text || 'Untitled' }}<template v-if="b.frozen"> ❄</template>
              </span>
            </div>
            <button v-if="hasFrozen" class="cd-check" role="checkbox" :aria-checked="keepFrozen" @click="keepFrozen = !keepFrozen">
              <span class="box" :class="{ on: keepFrozen }">{{ keepFrozen ? '✓' : '' }}</span>
              Keep frozen blocks (saved frozen, restored frozen)
            </button>
          </template>
          <template v-else>
            <div class="cd-msg">{{ message }}</div>
            <button class="cd-check" role="checkbox" :aria-checked="saveFirst" @click="saveFirst = !saveFirst">
              <span class="box" :class="{ on: saveFirst }">{{ saveFirst ? '✓' : '' }}</span>
              First save the current blocks as a combination
            </button>
          </template>
        </div>
        <div class="cd-acts">
          <button class="cancel" @click="emit('close')">Cancel</button>
          <button class="ok" :disabled="mode === 'save' && !nameText.trim()" @click="submit">
            {{ mode === 'save' ? 'Save' : 'Replace' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.cd-back{position:fixed;inset:0;z-index:2000;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:20px}
.cd-card{width:min(440px,100%);background:var(--surface-1);border:1px solid var(--border);border-radius:var(--radius-lg);box-shadow:0 18px 48px rgba(0,0,0,.5)}
.cd-hd{padding:16px 20px 0;font-size:15px;font-weight:650}
.cd-body{padding:14px 20px 4px;display:flex;flex-direction:column;gap:10px}
.cd-lbl{font-size:12px;font-weight:600;color:var(--text-dim)}
.cd-in{width:100%;font:inherit;font-size:13px;color:var(--text);background:var(--surface-2);border:1px solid var(--border);
  border-radius:var(--radius);padding:8px 10px;outline:none}
.cd-in:focus{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 30%,transparent)}
.cd-chips{display:flex;flex-wrap:wrap;gap:4px;max-height:132px;overflow-y:auto}
.cd-chip{display:inline-flex;align-items:center;gap:5px;font-size:11px;background:var(--surface-3);border:1px solid var(--border);
  border-radius:20px;padding:1px 9px;color:var(--text-dim);white-space:nowrap}
.cd-chip.neg{border-color:color-mix(in srgb,var(--danger) 40%,var(--border))}
.cd-chip i{width:7px;height:7px;border-radius:50%;display:inline-block;flex-shrink:0}
.cd-msg{font-size:13px;color:var(--text-dim);line-height:1.6}
.cd-check{display:flex;align-items:center;gap:9px;border:0;background:transparent;color:var(--text);font:inherit;font-size:12.5px;
  padding:2px 0;cursor:pointer;text-align:left}
.cd-check .box{width:16px;height:16px;flex-shrink:0;border-radius:4px;border:1.5px solid var(--border-strong);color:var(--on-accent);
  font-size:11px;display:flex;align-items:center;justify-content:center}
.cd-check .box.on{background:var(--accent);border-color:var(--accent)}
.cd-acts{display:flex;justify-content:flex-end;gap:10px;padding:16px 20px 18px}
.cd-acts button{border-radius:var(--radius);font:inherit;font-size:13px;font-weight:600;padding:8px 16px;cursor:pointer}
.cd-acts .cancel{border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim)}
.cd-acts .cancel:hover{border-color:var(--border-strong);color:var(--text)}
.cd-acts .ok{border:0;background:var(--accent);color:var(--on-accent)}
.cd-acts .ok:disabled{opacity:.5;cursor:default}
.cd-acts .ok:not(:disabled):hover{opacity:.92}
</style>
