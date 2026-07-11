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
  linkPin?: { nodeId: string; block: LibraryBlock; nonce: number } | null
}>()
const emit = defineEmits<{ navigate: [string]; 'save-block': [{ nodeId: string; block: LibraryBlock }] }>()
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

// A generation token guards against a stale stream landing in the wrong work: opening another work (or
// starting a new generation) bumps it, and the stream's callbacks / finally are ignored once superseded.
let genToken = 0
let genAbort: AbortController | null = null

async function onGenerate(payload: { positive: string; negative: string; snapshot: SnapshotData }) {
  if (busy.value) return
  if (!payload.positive.trim()) {
    error.value = 'Add at least one positive block to the generation zone.'
    return
  }
  const token = ++genToken
  genAbort = new AbortController()
  busy.value = true
  error.value = ''
  preview.value = ''
  const full = { ...params, prompt: payload.positive, negative_prompt: payload.negative }
  // The snapshot is the reproducible recipe: prompt composition + the generation params.
  const snapshot: SnapshotData = { ...payload.snapshot, params: { ...params } }
  try {
    await generateStream(full, (ev) => {
      if (token !== genToken) return // superseded (a work was opened / another generation started) → drop it
      if (ev.type === 'intermediate') {
        if (ev.samp === 0) preview.value = `data:${ev.mime};base64,${ev.image}`
      } else if (ev.type === 'final') {
        // Record the backend-resolved seed so the kept image is reproducible (params.seed may be null).
        const seed = ev.seed ?? full.seed
        const withSeed: SnapshotData = { ...snapshot, params: { ...snapshot.params, seed } }
        drafts.value = [
          { id: newId('img'), url: `data:${ev.mime};base64,${ev.image}`, params: { ...full, seed }, mock: ev.mock ?? false, snapshot: withSeed },
          ...drafts.value,
        ].slice(0, 50)
      } else if (ev.type === 'error') {
        error.value = ev.message
      }
    }, genAbort.signal)
  } catch (e) {
    if (token === genToken) error.value = e instanceof Error ? e.message : String(e) // ignore a superseded/aborted run
  } finally {
    if (token === genToken) { busy.value = false; preview.value = '' }
  }
}

// Stop the in-flight generation and invalidate its stream (its callbacks/finally then no-op).
function cancelGenerate() {
  genToken += 1
  genAbort?.abort()
  busy.value = false
  preview.value = ''
}

function onTake() {
  drafts.value.shift()
}

// After a save, the draft stack is on disk under this work — repoint its data: URLs at the vault so later
// saves don't re-serialize (up to 50) base64 images. Runs synchronously inside the save's dirty-suppression.
function onWorkSaved(workId: string) {
  drafts.value = drafts.value.map((d) => (d.url.startsWith('data:')
    ? { ...d, url: `/api/vault/works/${workId}/images/${d.id}`, file: `images/${d.id}.png` }
    : d))
}

// Open a saved work: pull its doc, restore params, hand the doc to the canvas to rebuild.
watch(() => props.openWorkId, async (raw) => {
  if (!raw) return
  cancelGenerate() // opening a work must not inherit an in-flight generation's output
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
      :insert-blocks="insertBlocks" :link-pin="linkPin" @generate="onGenerate" @take="onTake" @cancel="cancelGenerate"
      @saved="onWorkSaved" @navigate="emit('navigate', $event)" @save-block="emit('save-block', $event)" />
    <ParamsPanel :params="params" :open="panelOpen" @toggle="panelOpen = !panelOpen" />
  </div>
</template>

<style scoped>
.content { flex: 1; display: grid; grid-template-columns: 1fr 340px; min-width: 0; }
.content.collapsed { grid-template-columns: 1fr 46px; }
</style>
