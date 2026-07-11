<script setup lang="ts">
/* Presets tab (design/presets-mockup.html): a flat list of generation-param presets — code-shipped
 * built-ins (read-only) + user presets. Apply loads a preset's params into Generate; favourite /
 * default / duplicate / delete manage them. The New/Edit form lands in a later block. */
import { computed, onActivated, ref } from 'vue'
import { ApiError, deletePreset, listPresets, savePreset, setDefaultPreset, setPresetFavorite } from '../api'
import { modelLabel, samplerLabel } from '../presets/options'
import { filterPresets } from '../presets/list'
import { costPair } from '../presets/cost'
import PresetEditor from '../components/PresetEditor.vue'
import { useToast } from '../composables/useToast'
import { useConfirm } from '../composables/useConfirm'
import { newId } from '../vault/ids'
import type { Preset, PresetParams } from '../types'

const emit = defineEmits<{ apply: [Preset]; navigate: [string] }>()
const { push } = useToast()
const { confirm } = useConfirm()

const presets = ref<Preset[]>([])
const search = ref('')
const noVault = ref(false)

// Estimated Anlas per generation, shown for any paid tier and for Opus (which gets the first sample
// free at ≤1024²/≤28 steps). Tier-independent so a preset card is informative regardless of the viewer.
const cost = (p: Preset) => costPair(p.params)

async function load() {
  try {
    presets.value = await listPresets()
    noVault.value = false
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) { noVault.value = true; presets.value = [] }
    else push(e instanceof Error ? e.message : 'Could not load presets', 'err')
  }
}
onActivated(load) // fires on first mount under KeepAlive too

const groups = computed(() => filterPresets(presets.value, search.value))
const total = computed(() => presets.value.length)

function apply(p: Preset) {
  emit('apply', p)
  push(`Applied “${p.name}” — switched to Generate`, 'ok')
}
async function toggleFav(p: Preset) {
  try { await setPresetFavorite(p.id, !p.favorite); await load() }
  catch (e) { push(e instanceof Error ? e.message : 'Failed', 'err') }
}
async function makeDefault(p: Preset) {
  if (p.is_default) return
  try { await setDefaultPreset(p.id); await load() }
  catch (e) { push(e instanceof Error ? e.message : 'Failed', 'err') }
}
async function duplicate(p: Preset) {
  try {
    await savePreset({ id: newId('preset'), name: `${p.name} copy`, params: p.params })
    await load()
    push('Preset duplicated', 'ok')
  } catch (e) { push(e instanceof Error ? e.message : 'Failed', 'err') }
}
async function remove(p: Preset) {
  if (!(await confirm({ title: 'Delete preset', message: `Delete “${p.name}”? This can't be undone.`, confirmLabel: 'Delete', danger: true }))) return
  try { await deletePreset(p.id); await load(); push('Preset deleted', 'ok') }
  catch (e) { push(e instanceof Error ? e.message : 'Delete failed', 'err') }
}

// ---- editor drawer (new / edit) ----
const editor = ref<{ id: string | null; name: string; params: PresetParams } | null>(null)
function startParams(): PresetParams {
  // A new preset starts from the current default's params (a sensible baseline).
  const src = presets.value.find((p) => p.is_default) ?? presets.value.find((p) => p.builtin)
  return src ? { ...src.params } : ({} as PresetParams)
}
function openNew() { editor.value = { id: null, name: '', params: startParams() } }
function openEdit(p: Preset) { editor.value = { id: p.id, name: p.name, params: { ...p.params } } }
async function onSave(payload: { id: string | null; name: string; params: PresetParams }) {
  try {
    await savePreset({ id: payload.id ?? newId('preset'), name: payload.name, params: payload.params })
    editor.value = null
    await load()
    push(payload.id ? 'Preset saved' : 'Preset created', 'ok')
  } catch (e) { push(e instanceof Error ? e.message : 'Save failed', 'err') }
}
</script>

<template>
  <section class="presets">
    <div class="phead">
      <h1>Presets</h1><span class="count">· {{ total }} presets</span>
      <div class="spacer"></div>
      <input class="search" v-model="search" placeholder="Search presets…" />
      <button v-if="!noVault" class="newbtn" @click="openNew"><span>＋</span> New preset</button>
    </div>

    <div v-if="noVault" class="empty">
      <div class="emoji">◈</div>
      <p>No vault folder chosen yet. Pick one in <b>Settings</b> to save your own presets — the built-in starters live here regardless.</p>
      <button class="cta" @click="emit('navigate', 'settings')">Open Settings</button>
    </div>

    <div v-else class="pbody">
      <div v-if="groups.builtin.length" class="pgroup">
        <div class="ghd">Built-in</div>
        <div class="grid">
          <div v-for="p in groups.builtin" :key="p.id" class="pcard">
            <div class="ptop">
              <span class="pname">{{ p.name }}</span>
              <span v-if="p.is_default" class="dbadge">default</span>
              <span class="bbadge">built-in</span>
              <button class="star" :class="{ on: p.favorite }" title="Favourite" @click="toggleFav(p)">{{ p.favorite ? '★' : '☆' }}</button>
            </div>
            <div class="pmodel">{{ modelLabel(p.params.model) }}</div>
            <div class="pchips">
              <span class="pchip"><span class="ar" :class="{ land: p.params.width > p.params.height }"></span>{{ p.params.width }}×{{ p.params.height }}</span>
              <span class="pchip">{{ p.params.steps }} steps</span>
              <span class="pchip">CFG {{ p.params.scale.toFixed(1) }}</span>
              <span class="pchip">{{ samplerLabel(p.params.sampler) }}</span>
              <span class="pchip cost" title="Anlas per generation on any paid tier">◆ {{ cost(p).standard }}</span>
              <span v-if="cost(p).opus !== cost(p).standard" class="pchip cost opus" title="Opus tier — first sample free">{{ cost(p).opus === 0 ? 'Opus free' : `Opus ◆ ${cost(p).opus}` }}</span>
            </div>
            <div class="pacts">
              <button class="pbtn apply" @click="apply(p)">Apply</button>
              <div class="prow2">
                <button class="pbtn2" @click="duplicate(p)">Duplicate</button>
                <button class="pbtn2" :class="{ on: p.is_default }" @click="makeDefault(p)">{{ p.is_default ? '✓ Default' : 'Set default' }}</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div v-if="groups.user.length" class="pgroup">
        <div class="ghd">Your presets</div>
        <div class="grid">
          <div v-for="p in groups.user" :key="p.id" class="pcard">
            <div class="ptop">
              <span class="pname">{{ p.name }}</span>
              <span v-if="p.is_default" class="dbadge">default</span>
              <button class="star" :class="{ on: p.favorite }" title="Favourite" @click="toggleFav(p)">{{ p.favorite ? '★' : '☆' }}</button>
            </div>
            <div class="pmodel">{{ modelLabel(p.params.model) }}</div>
            <div class="pchips">
              <span class="pchip"><span class="ar" :class="{ land: p.params.width > p.params.height }"></span>{{ p.params.width }}×{{ p.params.height }}</span>
              <span class="pchip">{{ p.params.steps }} steps</span>
              <span class="pchip">CFG {{ p.params.scale.toFixed(1) }}</span>
              <span class="pchip">{{ samplerLabel(p.params.sampler) }}</span>
              <span class="pchip cost" title="Anlas per generation on any paid tier">◆ {{ cost(p).standard }}</span>
              <span v-if="cost(p).opus !== cost(p).standard" class="pchip cost opus" title="Opus tier — first sample free">{{ cost(p).opus === 0 ? 'Opus free' : `Opus ◆ ${cost(p).opus}` }}</span>
            </div>
            <div class="pacts">
              <button class="pbtn apply" @click="apply(p)">Apply</button>
              <div class="prow2">
                <button class="pbtn2" @click="openEdit(p)">Edit</button>
                <button class="pbtn2" @click="duplicate(p)">Duplicate</button>
                <button class="pbtn2" :class="{ on: p.is_default }" @click="makeDefault(p)">{{ p.is_default ? '✓ Default' : 'Set default' }}</button>
                <button class="pbtn2 del" title="Delete" @click="remove(p)">🗑</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div v-if="!groups.builtin.length && !groups.user.length" class="gridempty">
        <p>No presets match “{{ search }}”.</p>
      </div>
    </div>

    <PresetEditor v-if="editor" :model="editor" @save="onSave" @close="editor = null" />
  </section>
</template>

<style scoped>
.presets{flex:1;display:flex;flex-direction:column;min-width:0;background:var(--bg)}
.phead{display:flex;align-items:center;gap:10px;padding:14px 20px;border-bottom:1px solid var(--border);flex-shrink:0}
.phead h1{font-size:18px;font-weight:600;margin:0}
.phead .count{font-size:12px;color:var(--text-faint)}
.spacer{flex:1}
.search{width:240px;font:inherit;font-size:13px;color:var(--text);background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px;outline:none}
.search:focus{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 30%,transparent)}
.newbtn{background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--radius);font-weight:600;font-size:13px;padding:8px 14px;display:flex;align-items:center;gap:7px;cursor:pointer;flex-shrink:0}
.newbtn:hover{opacity:.92}

.empty{max-width:440px;margin:12vh auto;text-align:center;color:var(--text-dim)}
.empty .emoji{font-size:34px;margin-bottom:10px;color:var(--text-faint)}
.empty .cta{margin-top:14px;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text);border-radius:var(--radius);font-weight:600;font-size:13px;padding:8px 14px;cursor:pointer}
.empty .cta:hover{border-color:var(--accent);color:var(--accent)}

.pbody{flex:1;overflow-y:auto;padding:18px 20px;display:flex;flex-direction:column;gap:20px}
.ghd{font-size:11px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint);margin:0 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(248px,1fr));gap:10px}
.pcard{position:relative;display:flex;flex-direction:column;border:1px solid var(--border);border-radius:var(--radius-lg);background:var(--surface-1);padding:12px 13px 11px;transition:border-color .1s}
.pcard:hover{border-color:var(--border-strong)}
.ptop{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.pname{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.star{margin-left:auto;flex-shrink:0;border:0;background:transparent;color:var(--text-faint);cursor:pointer;font-size:14px;line-height:1}
.star.on{color:#e2b23a}
.dbadge{flex-shrink:0;font-size:9px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;color:var(--accent);border:1px solid color-mix(in srgb,var(--accent) 45%,var(--border));border-radius:10px;padding:0 6px}
.bbadge{flex-shrink:0;font-size:9px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;color:var(--text-faint);border:1px solid var(--border-strong);border-radius:10px;padding:0 6px}
.pmodel{font-size:11.5px;color:var(--text-dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pchips{display:flex;flex-wrap:wrap;gap:4px;margin:8px 0 2px}
.pchip{font-size:10.5px;color:var(--text-dim);background:var(--surface-3);border:1px solid var(--border);border-radius:20px;padding:1px 8px;font-variant-numeric:tabular-nums}
.pchip .ar{display:inline-block;width:8px;height:8px;border:1.5px solid var(--text-faint);border-radius:2px;vertical-align:-1px;margin-right:5px}
.pchip .ar.land{width:10px;height:7px}
.pchip.cost{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 40%,var(--border));font-weight:600}
/* Opus variant: a filled tint so "what Opus pays" reads as distinct from the standard cost chip. */
.pchip.cost.opus{color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,var(--surface-3));border-color:color-mix(in srgb,var(--accent) 45%,var(--border))}
/* pinned footer: margin-top:auto pushes it to the card bottom, so it never shifts with chip rows
   and lines up across a row (cards stretch to equal height). Apply is primary (full width); the
   secondary actions sit in a labelled row beneath. */
.pacts{display:flex;flex-direction:column;gap:6px;margin-top:auto;padding-top:12px}
.pbtn.apply{width:100%;border:0;background:var(--accent);color:var(--on-accent);border-radius:var(--radius);font:inherit;font-size:12px;font-weight:600;padding:6px;cursor:pointer}
.pbtn.apply:hover{opacity:.92}
.prow2{display:flex;gap:6px;flex-wrap:wrap}
.pbtn2{flex:1 1 auto;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);font:inherit;font-size:11px;font-weight:600;padding:5px 6px;cursor:pointer;white-space:nowrap}
.pbtn2:hover{color:var(--text);border-color:var(--border-strong)}
.pbtn2.on{background:var(--nav-active);color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,var(--border))}
.pbtn2.del{flex:0 0 32px}
.pbtn2.del:hover{color:var(--danger);border-color:var(--danger)}
.gridempty{color:var(--text-faint);font-size:13px;text-align:center;padding:30px}
</style>
