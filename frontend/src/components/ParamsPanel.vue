<script setup lang="ts">
import { computed } from 'vue'
import type { GenerateParams } from '../types'

const props = defineProps<{ params: GenerateParams; busy: boolean }>()
defineEmits<{ generate: [] }>()

const SIZES = [
  { name: 'Portrait', w: 832, h: 1216 },
  { name: 'Landscape', w: 1216, h: 832 },
  { name: 'Square', w: 1024, h: 1024 },
]

function setSize(w: number, h: number) {
  props.params.width = w
  props.params.height = h
}

const costHint = computed(() => {
  const free = props.params.steps <= 28 && props.params.width * props.params.height <= 1024 * 1024
  const prefix = free ? '≈ no Anlas' : 'uses Anlas'
  return `${prefix} · ${props.params.width}×${props.params.height} · ${props.params.steps} steps`
})
</script>

<template>
  <aside class="params">
    <div class="hd">Parameters</div>
    <div class="body">
      <div class="field">
        <span class="label">Model</span>
        <select v-model="params.model">
          <option value="nai-diffusion-4-5-full">NAI Diffusion 4.5 — Full</option>
          <option value="nai-diffusion-4-5-curated">NAI Diffusion 4.5 — Curated</option>
          <option value="nai-diffusion-3">NAI Diffusion 3</option>
        </select>
      </div>

      <div class="field">
        <span class="label">Prompt</span>
        <textarea class="prompt" v-model="params.prompt"></textarea>
      </div>

      <div class="field">
        <span class="label">Negative prompt</span>
        <textarea class="neg" v-model="params.negative_prompt"></textarea>
      </div>

      <div class="field">
        <span class="label">Size <span class="v">{{ params.width }} × {{ params.height }}</span></span>
        <div class="seg">
          <button
            v-for="s in SIZES"
            :key="s.name"
            :class="{ active: params.width === s.w && params.height === s.h }"
            @click="setSize(s.w, s.h)"
          >{{ s.name }}<span class="d">{{ s.w }}×{{ s.h }}</span></button>
        </div>
      </div>

      <details>
        <summary><span class="chev">▶</span> Advanced settings</summary>
        <div class="inner">
          <div class="row">
            <div class="field">
              <span class="label">Steps <span class="v">{{ params.steps }}</span></span>
              <input type="range" min="1" max="50" v-model.number="params.steps" />
            </div>
            <div class="field">
              <span class="label">Guidance <span class="v">{{ params.scale.toFixed(1) }}</span></span>
              <input type="range" min="0" max="10" step="0.5" v-model.number="params.scale" />
            </div>
          </div>
          <div class="row">
            <div class="field">
              <span class="label">Sampler</span>
              <select v-model="params.sampler">
                <option value="k_euler_ancestral">Euler Ancestral</option>
                <option value="k_euler">Euler</option>
                <option value="k_dpmpp_2m">DPM++ 2M</option>
              </select>
            </div>
            <div class="field">
              <span class="label">Images <span class="v">{{ params.n_samples }}</span></span>
              <input type="range" min="1" max="4" v-model.number="params.n_samples" />
            </div>
          </div>
          <div class="field">
            <span class="label">Seed</span>
            <div class="seedwrap">
              <input
                type="text"
                :value="params.seed ?? ''"
                placeholder="random"
                @input="params.seed = ($event.target as HTMLInputElement).value ? Number(($event.target as HTMLInputElement).value) : null"
              />
              <button class="mini" title="Randomize" @click="params.seed = Math.floor(Math.random() * 4294967295)">⚄</button>
              <button class="mini" title="Clear (random)" @click="params.seed = null">✕</button>
            </div>
          </div>
        </div>
      </details>
    </div>

    <div class="foot">
      <button class="generate" :disabled="busy" @click="$emit('generate')">
        <span>{{ busy ? 'Generating…' : 'Generate' }}</span>
        <small>{{ costHint }}</small>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.params{background:var(--surface-1);border-left:1px solid var(--border);display:flex;flex-direction:column;min-height:0}
.hd{padding:15px 18px;border-bottom:1px solid var(--border);font-weight:600;font-size:15px;flex-shrink:0}
.body{padding:16px 18px;overflow:auto;display:flex;flex-direction:column;gap:15px;flex:1}
.prompt{min-height:92px}.neg{min-height:52px}
details{border-top:1px solid var(--border);padding-top:12px}
summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:8px;
  font-size:12px;font-weight:600;color:var(--text-dim);user-select:none}
summary::-webkit-details-marker{display:none}
summary .chev{transition:transform .15s;font-size:11px}
details[open] summary .chev{transform:rotate(90deg)}
.inner{display:flex;flex-direction:column;gap:14px;padding-top:14px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.seedwrap{display:flex;gap:8px}.seedwrap input{flex:1}
.mini{width:36px;flex-shrink:0;border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);
  border-radius:var(--radius);cursor:pointer;font-size:14px}
.mini:hover{color:var(--text);border-color:var(--border-strong)}
.foot{padding:14px 18px;border-top:1px solid var(--border);flex-shrink:0}
.generate{width:100%;border:0;border-radius:var(--radius);cursor:pointer;background:var(--accent);color:var(--on-accent);
  font-weight:600;font-size:14px;padding:11px;display:flex;flex-direction:column;gap:1px;align-items:center}
.generate:hover:not(:disabled){background:var(--accent-strong)}
.generate:disabled{opacity:.65;cursor:default}
.generate small{font-weight:500;opacity:.85;font-size:11px}
</style>
