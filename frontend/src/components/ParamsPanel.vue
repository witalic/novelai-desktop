<script setup lang="ts">
import { computed, ref } from 'vue'
import Dropdown from './Dropdown.vue'
import type { PanelParams } from '../types'

const props = defineProps<{ params: PanelParams; open: boolean }>()
defineEmits<{ toggle: [] }>()

const MODELS = [
  { value: 'nai-diffusion-4-5-full', label: 'NAI Diffusion 4.5 — Full' },
  { value: 'nai-diffusion-4-5-curated', label: 'NAI Diffusion 4.5 — Curated' },
  { value: 'nai-diffusion-3', label: 'NAI Diffusion 3' },
]
// Sizes grouped by aspect ratio: portrait, then landscape, then square, then custom.
const SIZES = [
  { group: 'Portrait', items: [{ tier: 'Small', w: 512, h: 768 }, { tier: 'Normal', w: 832, h: 1216 }, { tier: 'Large', w: 1024, h: 1536 }, { tier: 'Wallpaper', w: 1088, h: 1920 }] },
  { group: 'Landscape', items: [{ tier: 'Small', w: 768, h: 512 }, { tier: 'Normal', w: 1216, h: 832 }, { tier: 'Large', w: 1536, h: 1024 }, { tier: 'Wallpaper', w: 1920, h: 1088 }] },
  { group: 'Square', items: [{ tier: 'Small', w: 640, h: 640 }, { tier: 'Normal', w: 1024, h: 1024 }, { tier: 'Large', w: 1472, h: 1472 }] },
]
const SAMPLERS = [
  { value: 'k_euler_ancestral', label: 'Euler Ancestral' },
  { value: 'k_euler', label: 'Euler' },
  { value: 'k_dpmpp_2s_ancestral', label: 'DPM++ 2S Ancestral' },
  { value: 'k_dpmpp_2m_sde', label: 'DPM++ 2M SDE' },
  { value: 'k_dpmpp_2m', label: 'DPM++ 2M' },
  { value: 'k_dpmpp_sde', label: 'DPM++ SDE' },
]
const UC_PRESETS = [
  { value: 4, label: 'Heavy' },
  { value: 5, label: 'Light' },
  { value: 7, label: 'Furry Focus' },
  { value: 6, label: 'Human Focus' },
  { value: 3, label: 'None' },
]
const NOISE = [
  { value: 'karras', label: 'karras (recommended)' },
  { value: 'exponential', label: 'exponential' },
  { value: 'polyexponential', label: 'polyexponential' },
]

const sizeOptions = computed(() => [
  ...SIZES.flatMap((g) => g.items.map((it) => ({ value: `${it.w},${it.h}`, label: `${it.tier} — ${it.w}×${it.h}`, group: g.group }))),
  { value: 'custom', label: 'Custom…', group: 'Other' },
])

const customMode = ref(false)
const matchedKey = computed(() => {
  for (const g of SIZES) for (const it of g.items) if (it.w === props.params.width && it.h === props.params.height) return `${it.w},${it.h}`
  return null
})
const sizeKey = computed<string>(() => (customMode.value || !matchedKey.value ? 'custom' : matchedKey.value))
function onSizePick(v: string | number) {
  if (v === 'custom') { customMode.value = true; return }
  customMode.value = false
  const [w, h] = String(v).split(',').map(Number)
  props.params.width = w
  props.params.height = h
}
const clampDim = (n: number) => Math.max(64, Math.min(2048, Math.round((n || 64) / 64) * 64))
</script>

<template>
  <aside class="params" :class="{ closed: !open }">
    <button v-if="!open" class="rail" title="Show settings" @click="$emit('toggle')"><span class="railicon">⚙</span></button>

    <template v-else>
      <div class="hd">
        <span>Settings</span>
        <button class="collapse" title="Hide" @click="$emit('toggle')">›</button>
      </div>
      <div class="body">
        <div class="field">
          <span class="label">Model</span>
          <Dropdown v-model="params.model" :options="MODELS" />
        </div>

        <div class="field">
          <span class="label">Size <span class="v">{{ params.width }} × {{ params.height }}</span></span>
          <Dropdown :model-value="sizeKey" :options="sizeOptions" @update:model-value="onSizePick" />
          <div v-if="sizeKey === 'custom'" class="row">
            <div class="field"><span class="label">Width</span>
              <input type="number" step="64" min="64" max="2048" :value="params.width"
                @change="params.width = clampDim(+($event.target as HTMLInputElement).value)" /></div>
            <div class="field"><span class="label">Height</span>
              <input type="number" step="64" min="64" max="2048" :value="params.height"
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
          <Dropdown :model-value="params.uc_preset" :options="UC_PRESETS" @update:model-value="params.uc_preset = Number($event)" />
        </div>

        <details open>
          <summary><span class="chev">▶</span> Advanced settings</summary>
          <div class="inner">
            <div class="row">
              <div class="field"><span class="label">Steps <span class="v">{{ params.steps }}</span></span><input type="range" min="1" max="50" v-model.number="params.steps" /></div>
              <div class="field"><span class="label">Guidance <span class="v">{{ params.scale.toFixed(1) }}</span></span><input type="range" min="0" max="10" step="0.5" v-model.number="params.scale" /></div>
            </div>
            <div class="field">
              <span class="label">Sampler</span>
              <Dropdown v-model="params.sampler" :options="SAMPLERS" />
            </div>
            <div class="field">
              <span class="label">Images <span class="v">{{ params.n_samples }}</span></span>
              <input type="range" min="1" max="4" v-model.number="params.n_samples" />
            </div>
            <div class="field">
              <span class="label">Seed</span>
              <div class="seedwrap">
                <input type="text" :value="params.seed ?? ''" placeholder="random"
                  @input="params.seed = ($event.target as HTMLInputElement).value ? Number(($event.target as HTMLInputElement).value) : null" />
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
              <Dropdown v-model="params.noise_schedule" :options="NOISE" />
            </div>
          </div>
        </details>
      </div>
    </template>
  </aside>
</template>

<style scoped>
.params{background:var(--surface-1);border-left:1px solid var(--border);display:flex;flex-direction:column;min-height:0;order:1}
.rail{width:100%;height:100%;border:0;background:transparent;color:var(--text-dim);cursor:pointer;display:flex;justify-content:center;padding-top:16px}
.rail:hover{color:var(--text)}.railicon{font-size:18px}
.hd{display:flex;align-items:center;justify-content:space-between;padding:15px 18px;border-bottom:1px solid var(--border);font-weight:600;font-size:15px;flex-shrink:0}
.collapse{border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);width:26px;height:26px;cursor:pointer}
.collapse:hover{color:var(--text);border-color:var(--border-strong)}
.body{padding:16px 18px;overflow:auto;display:flex;flex-direction:column;gap:15px;flex:1}
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
