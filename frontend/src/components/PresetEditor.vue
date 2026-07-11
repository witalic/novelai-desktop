<script setup lang="ts">
/* Preset editor drawer — new / edit / duplicate / save-current. Reuses ParamFields (seed hidden;
 * a preset stores params only). The parent owns open/close; this emits save/close. */
import { reactive, ref } from 'vue'
import ParamFields from './ParamFields.vue'
import { stripSeed } from '../presets/diff'
import type { PanelParams, PresetParams } from '../types'

const props = defineProps<{ model: { id: string | null; name: string; params: PresetParams } }>()
const emit = defineEmits<{ save: [{ id: string | null; name: string; params: PresetParams }]; close: [] }>()

const name = ref(props.model.name)
// ParamFields wants a full PanelParams; seed is unused (hidden) and stripped on save.
const params = reactive<PanelParams>({ ...props.model.params, seed: null })
const nameError = ref(false)

function save() {
  if (!name.value.trim()) { nameError.value = true; return }
  emit('save', { id: props.model.id, name: name.value.trim(), params: stripSeed(params) })
}
</script>

<template>
  <div class="back" @click="emit('close')"></div>
  <div class="drawer">
    <div class="dhd">{{ model.id ? '✎ Edit preset' : '＋ New preset' }}<span class="x" @click="emit('close')">✕</span></div>
    <div class="dbody">
      <div class="field">
        <span class="label">Name <span v-if="nameError" class="req">· required</span></span>
        <input v-model="name" placeholder="Preset name" :class="{ err: nameError }" @input="nameError = false" />
      </div>
      <ParamFields :params="params" hide-seed />
    </div>
    <div class="dfoot">
      <span class="sp"></span>
      <button class="cancel" @click="emit('close')">Cancel</button>
      <button class="save" @click="save">Save preset</button>
    </div>
  </div>
</template>

<style scoped>
.back{position:fixed;inset:0;z-index:900;background:rgba(0,0,0,.35)}
.drawer{position:fixed;top:0;right:0;bottom:0;z-index:901;width:min(400px,92vw);display:flex;flex-direction:column;
  background:var(--surface-1);border-left:1px solid var(--border);box-shadow:-8px 0 30px rgba(0,0,0,.4)}
.dhd{display:flex;align-items:center;justify-content:space-between;padding:15px 18px;border-bottom:1px solid var(--border);font-weight:600;font-size:15px;flex-shrink:0}
.dhd .x{cursor:pointer;color:var(--text-faint);font-size:13px}
.dhd .x:hover{color:var(--text)}
.dbody{flex:1;overflow-y:auto;padding:16px 18px;display:flex;flex-direction:column;gap:15px}
.dbody input{width:100%;font:inherit;color:var(--text);background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px;outline:none}
.dbody input:focus{border-color:var(--accent)}
.dbody input.err{border-color:var(--danger)}
.req{color:var(--danger);font-weight:500}
.dfoot{display:flex;align-items:center;gap:8px;padding:12px 18px;border-top:1px solid var(--border);flex-shrink:0}
.dfoot .sp{flex:1}
.cancel{border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);font:inherit;font-size:13px;font-weight:600;padding:8px 14px;cursor:pointer}
.cancel:hover{color:var(--text);border-color:var(--border-strong)}
.save{border:0;background:var(--accent);color:var(--on-accent);border-radius:var(--radius);font:inherit;font-size:13px;font-weight:600;padding:8px 16px;cursor:pointer}
.save:hover{opacity:.92}
</style>
