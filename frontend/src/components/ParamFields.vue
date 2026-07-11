<script setup lang="ts">
/* The generation-param form (model/size/quality/uc + advanced). Extracted from ParamsPanel so the
 * Tools panel and the preset editor share one set of controls. Mutates `params` in place.
 * `hideSeed` drops the seed field — a preset has no seed. */
import { computed, ref } from 'vue'
import Dropdown from './Dropdown.vue'
import { useCatalog } from '../composables/useCatalog'
import type { PanelParams } from '../types'

const props = defineProps<{ params: PanelParams; hideSeed?: boolean }>()
const { catalog } = useCatalog()

// A seed is a non-negative 32-bit integer; anything else (letters, overflow) clears it → random, so the
// user never silently gets a random seed from a typo they think stuck.
function parseSeed(v: string): number | null {
  const s = v.trim()
  if (!/^\d+$/.test(s)) return null
  const n = Number(s)
  return Number.isSafeInteger(n) && n <= 4294967295 ? n : null
}

// The selected model (may be absent from the catalog for an old preset/work — see the synthetic option).
const currentModel = computed(() => catalog.value?.models.find((m) => m.id === props.params.model) ?? null)

// A stored value not in the catalog gets a synthetic "(unavailable)" option so it stays selected and
// visible — never silently reset (design-prefs). Model dropdown:
const modelOptions = computed(() => {
  const cat = catalog.value
  if (!cat) return []
  const opts = cat.models.map((m) => ({ value: m.id, label: m.label }))
  if (!cat.models.some((m) => m.id === props.params.model)) {
    opts.unshift({ value: props.params.model, label: `(unavailable) ${props.params.model}` })
  }
  return opts
})

// Sampler dropdown, filtered to the selected model's samplers (SMEA is v3-only, so v4 never lists it).
const samplerOptions = computed(() => {
  const cat = catalog.value
  if (!cat) return []
  const allowed = currentModel.value?.samplers ?? cat.samplers.map((s) => s.id)
  const opts = cat.samplers.filter((s) => allowed.includes(s.id)).map((s) => ({ value: s.id, label: s.label }))
  if (!opts.some((o) => o.value === props.params.sampler)) {
    const known = cat.samplers.find((s) => s.id === props.params.sampler)
    opts.unshift({ value: props.params.sampler, label: known ? `${known.label} (unavailable)` : `(unavailable) ${props.params.sampler}` })
  }
  return opts
})

const ucOptions = computed(() => catalog.value?.uc_presets.map((u) => ({ value: u.value, label: u.label })) ?? [])
const noiseOptions = computed(() => catalog.value?.noise_schedules.map((n) => ({ value: n.value, label: n.label })) ?? [])

const sizeOptions = computed(() => {
  const cat = catalog.value
  if (!cat) return []
  return [
    ...cat.resolutions.map((r) => ({ value: `${r.width},${r.height}`, label: `${r.tier} — ${r.width}×${r.height}`, group: r.group })),
    { value: 'custom', label: 'Custom…', group: 'Other' },
  ]
})

const customMode = ref(false)
const matchedKey = computed(() => {
  const r = catalog.value?.resolutions.find((x) => x.width === props.params.width && x.height === props.params.height)
  return r ? `${r.width},${r.height}` : null
})
const sizeKey = computed<string>(() => (customMode.value || !matchedKey.value ? 'custom' : matchedKey.value))
function onSizePick(v: string | number) {
  if (v === 'custom') { customMode.value = true; return }
  customMode.value = false
  const [w, h] = String(v).split(',').map(Number)
  props.params.width = w
  props.params.height = h
}

// Ranges + dimension limits from the catalog (fallbacks keep the panel usable before the fetch resolves).
const stepsRange = computed(() => currentModel.value?.steps ?? { min: 1, max: 50, step: 1, default: 28 })
const scaleRange = computed(() => currentModel.value?.scale ?? { min: 0, max: 10, step: 0.5, default: 5 })
const dim = computed(() => catalog.value?.dim_limits ?? { min: 64, max: 2048, step: 64 })
const clampDim = (n: number) => {
  const d = dim.value
  return Math.max(d.min, Math.min(d.max, Math.round((n || d.min) / d.step) * d.step))
}
</script>

<template>
  <div v-if="!catalog" class="fields loading">Loading options…</div>
  <div v-else class="fields">
    <div class="field">
      <span class="label">Model</span>
      <Dropdown v-model="params.model" :options="modelOptions" />
    </div>

    <div class="field">
      <span class="label">Size <span class="v">{{ params.width }} × {{ params.height }}</span></span>
      <Dropdown :model-value="sizeKey" :options="sizeOptions" @update:model-value="onSizePick" />
      <div v-if="sizeKey === 'custom'" class="row">
        <div class="field"><span class="label">Width</span>
          <input type="number" :step="dim.step" :min="dim.min" :max="dim.max" :value="params.width"
            @change="params.width = clampDim(+($event.target as HTMLInputElement).value)" /></div>
        <div class="field"><span class="label">Height</span>
          <input type="number" :step="dim.step" :min="dim.min" :max="dim.max" :value="params.height"
            @change="params.height = clampDim(+($event.target as HTMLInputElement).value)" /></div>
      </div>
    </div>

    <div class="setting">
      <div class="txt"><div class="name">Add quality tags</div><div class="desc">Prepend quality tags to the prompt.</div></div>
      <button class="switch" :class="{ on: params.quality_toggle }" role="switch" :aria-checked="params.quality_toggle"
        @click="params.quality_toggle = !params.quality_toggle"><span class="knob"></span></button>
    </div>

    <div class="field">
      <span class="label">Undesired content preset</span>
      <Dropdown :model-value="params.uc_preset" :options="ucOptions" @update:model-value="params.uc_preset = Number($event)" />
    </div>

    <details open>
      <summary><span class="chev">▶</span> Advanced settings</summary>
      <div class="inner">
        <div class="row">
          <div class="field"><span class="label">Steps <span class="v">{{ params.steps }}</span></span><input type="range" :min="stepsRange.min" :max="stepsRange.max" :step="stepsRange.step" v-model.number="params.steps" /></div>
          <div class="field"><span class="label">Guidance <span class="v">{{ params.scale.toFixed(1) }}</span></span><input type="range" :min="scaleRange.min" :max="scaleRange.max" :step="scaleRange.step" v-model.number="params.scale" /></div>
        </div>
        <div class="field">
          <span class="label">Sampler</span>
          <Dropdown v-model="params.sampler" :options="samplerOptions" />
        </div>
        <div class="field">
          <span class="label">Images <span class="v">{{ params.n_samples }}</span></span>
          <input type="range" min="1" max="4" v-model.number="params.n_samples" />
        </div>
        <div v-if="!hideSeed" class="field">
          <span class="label">Seed <span class="v" style="color:var(--text-faint);font-weight:500">not in presets</span></span>
          <div class="seedwrap">
            <input type="text" :value="params.seed ?? ''" placeholder="random"
              @input="params.seed = parseSeed(($event.target as HTMLInputElement).value)" />
            <button class="mini" title="Randomize" @click="params.seed = Math.floor(Math.random() * 4294967295)">⚄</button>
            <button class="mini" title="Clear" @click="params.seed = null">✕</button>
          </div>
        </div>
        <div class="field">
          <span class="label">Prompt guidance rescale <span class="v">{{ params.cfg_rescale.toFixed(2) }}</span></span>
          <input type="range" min="0" max="1" step="0.02" v-model.number="params.cfg_rescale" />
        </div>
        <div class="field">
          <span class="label">Noise schedule</span>
          <Dropdown v-model="params.noise_schedule" :options="noiseOptions" />
        </div>
      </div>
    </details>
  </div>
</template>

<style scoped>
.fields{display:flex;flex-direction:column;gap:15px}
.fields.loading{color:var(--text-faint);font-size:12px;padding:4px 0}
.setting{display:flex;align-items:center;justify-content:space-between;gap:12px}
.setting .name{font-weight:500;font-size:13px}
.setting .desc{font-size:11px;color:var(--text-dim);margin-top:2px}
.switch{width:38px;height:22px;flex-shrink:0;border-radius:20px;border:1px solid var(--border);background:var(--surface-3);position:relative;cursor:pointer;padding:0}
.switch.on{background:var(--accent);border-color:var(--accent)}
.switch .knob{position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;transition:left .15s}
.switch.on .knob{left:18px}
details{border-top:1px solid var(--border);padding-top:12px}
summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:8px;font-size:12px;font-weight:600;color:var(--text-dim)}
summary::-webkit-details-marker{display:none}
summary .chev{transition:transform .15s;font-size:11px}
details[open] summary .chev{transform:rotate(90deg)}
.inner{display:flex;flex-direction:column;gap:14px;padding-top:14px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.seedwrap{display:flex;gap:8px}.seedwrap input{flex:1}
.mini{width:36px;flex-shrink:0;border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);cursor:pointer;font-size:14px}
.mini:hover{color:var(--text);border-color:var(--border-strong)}
</style>
