<script setup lang="ts">
import { computed, onActivated, onMounted, reactive, ref, watch } from 'vue'
import ToolsPanel from '../components/ToolsPanel.vue'
import PresetEditor from '../components/PresetEditor.vue'
import CanvasBoard from '../components/CanvasBoard.vue'
import { generateStream, listPresets, loadWork, savePreset } from '../api'
import { resolveDefaultId, stripSeed } from '../presets/diff'
import { useAccount } from '../composables/useAccount'
import { useToast } from '../composables/useToast'
import { newId } from '../vault/ids'
import { workToDrafts } from '../vault/serialize'
import type { PanelParams, PresetParams, Preset, GenResult, WorkDoc, SnapshotData } from '../types'

const props = defineProps<{
  openWorkId?: string | null
  applyPreset?: { id: string; params: PresetParams; nonce: number } | null
}>()
const emit = defineEmits<{ navigate: [string]; 'open-library': [{ category: string; tags: string[] }] }>()
const { push } = useToast()
const account = useAccount()

const params = reactive<PanelParams>({
  model: 'nai-diffusion-5-full', width: 832, height: 1216, steps: 28, scale: 7,
  sampler: 'k_euler_ancestral', seed: null, n_samples: 1, noise_schedule: 'karras',
  cfg_rescale: 0, quality_toggle: true, uc_preset: 4, dedupe: false,
})

// ---- presets: the Tools panel's Preset strip picks/updates; the default seeds a fresh work ----
const presets = ref<Preset[]>([])
const activePresetId = ref<string | null>(null)
const defaultParams = ref<PresetParams | null>(null)
async function loadPresets() {
  try {
    presets.value = await listPresets()
    const id = resolveDefaultId(presets.value)
    defaultParams.value = presets.value.find((p) => p.id === id)?.params ?? null
    return id
  } catch { return null /* no vault / offline — keep the hardcoded defaults */ }
}
// Seed the default into a brand-new session only (never clobber a work being opened).
onMounted(async () => {
  const defId = await loadPresets()
  if (!props.openWorkId && defaultParams.value) { Object.assign(params, defaultParams.value); activePresetId.value = defId }
})
onActivated(loadPresets) // pick up presets created/edited in the Presets tab while we were away

// Apply a preset (from the Presets tab): overwrite params (seed absent ⇒ seed untouched).
watch(() => props.applyPreset?.nonce, () => {
  if (props.applyPreset) { Object.assign(params, props.applyPreset.params); activePresetId.value = props.applyPreset.id }
})

// New work (from the canvas) starts clean: drop the previous work's generation stack (it lives here,
// not in the canvas nodes CanvasBoard already reset) and re-seed params from the current default preset.
function onNewWork() {
  drafts.value = []
  loadedWork.value = null
  if (defaultParams.value) Object.assign(params, defaultParams.value)
}

// Preset strip: switch the active preset from the panel dropdown.
function onPickPreset(p: Preset) { Object.assign(params, p.params); activePresetId.value = p.id }
// Update the active (user) preset with the live params.
async function onUpdatePreset() {
  const p = presets.value.find((x) => x.id === activePresetId.value)
  if (!p || p.builtin) return
  try { await savePreset({ id: p.id, name: p.name, params: stripSeed(params) }); await loadPresets(); push('Preset updated', 'ok') }
  catch (e) { push(e instanceof Error ? e.message : 'Update failed', 'err') }
}
// "Save as preset…" opens the editor prefilled from the live params.
const presetDraft = ref<{ id: string | null; name: string; params: PresetParams } | null>(null)
function onSaveAsPreset() { presetDraft.value = { id: null, name: '', params: stripSeed(params) } }
async function onPresetDraftSave(payload: { id: string | null; name: string; params: PresetParams }) {
  const id = payload.id ?? newId('preset')
  try {
    await savePreset({ id, name: payload.name, params: payload.params })
    presetDraft.value = null
    await loadPresets()
    activePresetId.value = id // the just-saved preset is now the active one
    push('Preset created', 'ok')
  } catch (e) { push(e instanceof Error ? e.message : 'Save failed', 'err') }
}

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
    if (token === genToken) { busy.value = false; preview.value = ''; account.refresh() } // reconcile Anlas after a real spend
  }
}

// Stop the in-flight generation and invalidate its stream (its callbacks/finally then no-op).
function cancelGenerate() {
  genToken += 1
  genAbort?.abort()
  busy.value = false
  preview.value = ''
}

// A draft was kept on the canvas — remove that one from the stack (id given by a panel-thumbnail
// drag; bare = the top, from the station Output slot).
function onTake(id?: string) {
  drafts.value = id ? drafts.value.filter((d) => d.id !== id) : drafts.value.slice(1)
}
function onClearStack() { drafts.value = [] }
function onRemoveMany(ids: string[]) { drafts.value = drafts.value.filter((d) => !ids.includes(d.id)) }

// Keep several drafts on the canvas at once (ordered), driven from the Stack tab. CanvasBoard
// materialises them into the gallery and emits `take` per id, which prunes them from the stack.
const keepSignal = ref<{ ids: string[]; nonce: number } | null>(null)
function onKeepMany(ids: string[]) { keepSignal.value = { ids, nonce: Date.now() } }

// After a save, the draft stack is on disk under this work — repoint its data: URLs at the vault so later
// saves don't re-serialize (up to 50) base64 images. Runs synchronously inside the save's dirty-suppression.
function onWorkSaved(workId: string) {
  drafts.value = drafts.value.map((d) => (d.url.startsWith('data:')
    ? { ...d, url: `/api/vault/works/${workId}/images/${d.id}`, file: `images/${d.id}.png` }
    : d))
}

// Open a saved work: pull its doc, restore params + the generation stack, hand the doc to the canvas to
// rebuild. Shared by the open-work signal and the canvas's conflict reload (so the FULL work refreshes).
async function loadWorkIntoView(id: string) {
  cancelGenerate() // opening/reloading a work must not inherit an in-flight generation's output
  try {
    const doc = await loadWork(id)
    Object.assign(params, (doc.params as Partial<PanelParams>) || {})
    drafts.value = workToDrafts(doc)
    loadedWork.value = doc // the canvas watches this and rebuilds
  } catch (e) {
    push(e instanceof Error ? e.message : 'Could not open work', 'err')
  }
}
watch(() => props.openWorkId, (raw) => {
  if (!raw) return
  loadWorkIntoView(raw.split('#')[0]) // App appends '#<ts>' to force reopen of the same work
})
// A concurrent edit was detected on save (409) → re-fetch the whole work, params/stack included (#4).
function onReloadWork() { if (loadedWork.value) loadWorkIntoView(loadedWork.value.id) }
</script>

<template>
  <div class="content" :class="{ collapsed: !panelOpen }">
    <CanvasBoard :drafts="drafts" :busy="busy" :error="error" :preview="preview" :params="params" :open-work="loadedWork"
      :keep-drafts="keepSignal" @generate="onGenerate" @take="onTake" @cancel="cancelGenerate"
      @saved="onWorkSaved" @navigate="emit('navigate', $event)" @open-library="emit('open-library', $event)"
      @new-work="onNewWork" @reload-work="onReloadWork" />
    <ToolsPanel :params="params" :open="panelOpen" :drafts="drafts" :busy="busy" :presets="presets" :active-preset-id="activePresetId"
      @toggle="panelOpen = !panelOpen" @pick-preset="onPickPreset" @update-preset="onUpdatePreset" @save-as="onSaveAsPreset"
      @clear-stack="onClearStack" @keep-many="onKeepMany" @remove-many="onRemoveMany" @navigate="emit('navigate', $event)" />
    <PresetEditor v-if="presetDraft" :model="presetDraft" @save="onPresetDraftSave" @close="presetDraft = null" />
  </div>
</template>

<style scoped>
.content { flex: 1; display: grid; grid-template-columns: 1fr 340px; min-width: 0; }
.content.collapsed { grid-template-columns: 1fr 46px; }
</style>
