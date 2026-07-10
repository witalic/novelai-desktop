<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import ParamsPanel from '../components/ParamsPanel.vue'
import CanvasBoard from '../components/CanvasBoard.vue'
import { generateStream, loadWork } from '../api'
import { useToast } from '../composables/useToast'
import { newId } from '../vault/ids'
import { workToDrafts } from '../vault/serialize'
import type { PanelParams, GenResult, WorkDoc, SnapshotData, LibraryBlock } from '../types'

const props = defineProps<{
  openWorkId?: string | null
  insertBlocks?: { blocks: LibraryBlock[]; nonce: number } | null
}>()
const emit = defineEmits<{ navigate: [string] }>()
const { push } = useToast()

const params = reactive<PanelParams>({
  model: 'nai-diffusion-4-5-full', width: 832, height: 1216, steps: 28, scale: 5,
  sampler: 'k_euler_ancestral', seed: null, n_samples: 1, noise_schedule: 'karras',
  cfg_rescale: 0, quality_toggle: true, uc_preset: 4,
})

const panelOpen = ref(false) // model/size settings start hidden — the canvas is the focus
const drafts = ref<GenResult[]>([])
const preview = ref('')
const busy = ref(false)
const error = ref('')
const loadedWork = ref<WorkDoc | null>(null)

async function onGenerate(payload: { positive: string; negative: string; snapshot: SnapshotData }) {
  if (busy.value) return
  if (!payload.positive.trim()) {
    error.value = 'Add at least one positive block to the generation zone.'
    return
  }
  busy.value = true
  error.value = ''
  preview.value = ''
  const full = { ...params, prompt: payload.positive, negative_prompt: payload.negative }
  // The snapshot is the reproducible recipe: prompt composition + the generation params.
  const snapshot: SnapshotData = { ...payload.snapshot, params: { ...params } }
  try {
    await generateStream(full, (ev) => {
      if (ev.type === 'intermediate') {
        if (ev.samp === 0) preview.value = `data:${ev.mime};base64,${ev.image}`
      } else if (ev.type === 'final') {
        drafts.value = [
          { id: newId('img'), url: `data:${ev.mime};base64,${ev.image}`, params: { ...full }, mock: false, snapshot },
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

// Open a saved work: pull its doc, restore params, hand the doc to the canvas to rebuild.
watch(() => props.openWorkId, async (raw) => {
  if (!raw) return
  const id = raw.split('#')[0] // App appends '#<ts>' to force reopen of the same work
  try {
    const doc = await loadWork(id)
    Object.assign(params, (doc.params as Partial<PanelParams>) || {})
    drafts.value = workToDrafts(doc) // restore the generation stack
    loadedWork.value = doc
  } catch (e) {
    push(e instanceof Error ? e.message : 'Could not open work', 'err')
  }
})
</script>

<template>
  <div class="content" :class="{ collapsed: !panelOpen }">
    <CanvasBoard :drafts="drafts" :busy="busy" :error="error" :preview="preview" :params="params" :open-work="loadedWork"
      :insert-blocks="insertBlocks" @generate="onGenerate" @take="onTake" @navigate="emit('navigate', $event)" />
    <ParamsPanel :params="params" :open="panelOpen" @toggle="panelOpen = !panelOpen" />
  </div>
</template>

<style scoped>
.content { flex: 1; display: grid; grid-template-columns: 1fr 340px; min-width: 0; }
.content.collapsed { grid-template-columns: 1fr 46px; }
</style>
