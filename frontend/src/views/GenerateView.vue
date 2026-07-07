<script setup lang="ts">
import { reactive, ref } from 'vue'
import ParamsPanel from '../components/ParamsPanel.vue'
import CanvasBoard from '../components/CanvasBoard.vue'
import { generateStream } from '../api'
import type { PanelParams, GenResult } from '../types'

const params = reactive<PanelParams>({
  model: 'nai-diffusion-4-5-full',
  width: 832,
  height: 1216,
  steps: 28,
  scale: 5,
  sampler: 'k_euler_ancestral',
  seed: null,
  n_samples: 1,
  noise_schedule: 'karras',
  cfg_rescale: 0,
  quality_toggle: true,
  uc_preset: 4,
})

const panelOpen = ref(true)
// Stack (LIFO) of finished-but-not-taken images; only the top is rendered in the output slot.
const drafts = ref<GenResult[]>([])
const preview = ref('')
const busy = ref(false)
const error = ref('')
let counter = 0

async function onGenerate(composed: { positive: string; negative: string }) {
  if (busy.value) return
  if (!composed.positive.trim()) {
    error.value = 'Add at least one positive block to the generation zone.'
    return
  }
  busy.value = true
  error.value = ''
  preview.value = ''
  const full = { ...params, prompt: composed.positive, negative_prompt: composed.negative }
  try {
    await generateStream(full, (ev) => {
      if (ev.type === 'intermediate') {
        // Preview follows a single sample (samp 0) so multi-image runs don't flicker between images.
        if (ev.samp === 0) preview.value = `data:${ev.mime};base64,${ev.image}`
      } else if (ev.type === 'final') {
        drafts.value = [
          { id: ++counter, url: `data:${ev.mime};base64,${ev.image}`, params: { ...full }, mock: false },
          ...drafts.value,
        ].slice(0, 50)
      } else if (ev.type === 'error') {
        error.value = ev.message
      }
    })
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
    preview.value = ''
  }
}

function onTake() {
  drafts.value.shift()
}
</script>

<template>
  <div class="content" :class="{ collapsed: !panelOpen }">
    <CanvasBoard :drafts="drafts" :busy="busy" :error="error" :preview="preview" @generate="onGenerate" @take="onTake" />
    <ParamsPanel :params="params" :open="panelOpen" @toggle="panelOpen = !panelOpen" />
  </div>
</template>

<style scoped>
.content { flex: 1; display: grid; grid-template-columns: 1fr 340px; min-width: 0; }
.content.collapsed { grid-template-columns: 1fr 46px; }
</style>
