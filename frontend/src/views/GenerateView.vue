<script setup lang="ts">
import { reactive, ref } from 'vue'
import ParamsPanel from '../components/ParamsPanel.vue'
import ResultStage from '../components/ResultStage.vue'
import { generate } from '../api'
import type { GenerateParams, GenResult } from '../types'

const params = reactive<GenerateParams>({
  prompt: '1girl, silver hair, blue eyes, ornate dress, cinematic lighting, masterpiece, best quality',
  negative_prompt: 'lowres, bad anatomy, worst quality',
  model: 'nai-diffusion-4-5-full',
  width: 832,
  height: 1216,
  steps: 28,
  scale: 5,
  sampler: 'k_euler_ancestral',
  seed: null,
  n_samples: 1,
})

const results = ref<GenResult[]>([])
const selected = ref<GenResult | null>(null)
const busy = ref(false)
const error = ref('')
let counter = 0

async function onGenerate() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const resp = await generate({ ...params })
    const created: GenResult[] = resp.images.map((b64) => ({
      id: ++counter,
      url: `data:image/png;base64,${b64}`,
      params: { ...params },
      mock: resp.mock,
    }))
    results.value = [...created, ...results.value].slice(0, 24)
    selected.value = created[0] ?? selected.value
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="content">
    <ResultStage
      :selected="selected"
      :results="results"
      :busy="busy"
      :error="error"
      @select="selected = $event"
    />
    <ParamsPanel :params="params" :busy="busy" @generate="onGenerate" />
  </div>
</template>

<style scoped>
.content { flex: 1; display: grid; grid-template-columns: 1fr 340px; min-width: 0; }
</style>
