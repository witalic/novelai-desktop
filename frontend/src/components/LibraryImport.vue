<script setup lang="ts">
/* Bulk-import prompt blocks into the vault (design/library-import-mockup.html). Drop / browse JSON &
 * ZIP files or pick a folder → the backend parses + flags duplicates → review by category with
 * multi-select → save. New blocks get a fresh id; a duplicate can be skipped or replace-in-place. */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { parseImport, importBlocks } from '../api'
import { browseFolder } from '../electron'
import { useToast } from '../composables/useToast'
import { newId } from '../vault/ids'
import type { CategoryCount, ImportCandidate, ImportSkip, LibraryBlock } from '../types'

const props = defineProps<{ categories: CategoryCount[] }>()
const emit = defineEmits<{ close: []; done: [] }>()
const { push } = useToast()

interface Row extends ImportCandidate { sel: boolean; mode: 'skip' | 'replace' }
const rows = ref<Row[]>([])
const skipped = ref<ImportSkip[]>([])
const busy = ref(false)
const dragOver = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const stage = computed(() => (rows.value.length || skipped.value.length ? 'review' : 'drop'))
const newRows = computed(() => rows.value.filter((r) => !r.existing_id))
const dupRows = computed(() => rows.value.filter((r) => r.existing_id))
const newGroups = computed(() => {
  const m = new Map<string, Row[]>()
  for (const r of newRows.value) { if (!m.has(r.category)) m.set(r.category, []); m.get(r.category)!.push(r) }
  return [...m.entries()].map(([category, items]) => ({ category, items }))
})
const newSel = computed(() => newRows.value.filter((r) => r.sel).length)
const repSel = computed(() => dupRows.value.filter((r) => r.mode === 'replace').length)
const saveCount = computed(() => newSel.value + repSel.value)
const allNewSel = computed(() => newRows.value.length > 0 && newRows.value.every((r) => r.sel))

const catColor = (slug: string) => props.categories.find((c) => c.slug === slug)?.color || '#738496'
const catName = (slug: string) => props.categories.find((c) => c.slug === slug)?.name || slug.replace(/[-_]/g, ' ')

function toggleAllNew() { const v = !allNewSel.value; newRows.value.forEach((r) => { r.sel = v }) }
function toggleGroup(items: Row[]) { const v = !items.every((r) => r.sel); items.forEach((r) => { r.sel = v }) }

// ---- sources → parse ----
async function runParse(payload: { texts?: string[]; zip_b64?: string; path?: string }) {
  busy.value = true
  try {
    const res = await parseImport(payload)
    for (const c of res.candidates) rows.value.push({ ...c, sel: !c.existing_id, mode: 'skip' })
    skipped.value.push(...res.skipped)
    if (!res.candidates.length && !res.skipped.length) push('No blocks found in the import', 'err')
  } catch (e) {
    push(e instanceof Error ? e.message : 'Could not read the import', 'err')
  } finally { busy.value = false }
}
async function toB64(file: File): Promise<string> {
  const buf = new Uint8Array(await file.arrayBuffer())
  let bin = ''
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000))
  return btoa(bin)
}
async function onFiles(files: File[]) {
  const jsons = files.filter((f) => /\.json$/i.test(f.name))
  const zips = files.filter((f) => /\.zip$/i.test(f.name))
  const other = files.length - jsons.length - zips.length
  if (other) push(`Ignored ${other} file${other > 1 ? 's' : ''} that aren't .json or .zip`, 'err')
  if (jsons.length) await runParse({ texts: await Promise.all(jsons.map((f) => f.text())) })
  for (const z of zips) await runParse({ zip_b64: await toB64(z) })
}
function onDrop(e: DragEvent) {
  dragOver.value = false
  const files = [...(e.dataTransfer?.files ?? [])]
  if (files.length) onFiles(files)
}
function onPick(e: Event) {
  const el = e.target as HTMLInputElement
  const files = [...(el.files ?? [])]
  el.value = '' // let the same file be re-picked later
  if (files.length) onFiles(files)
}
async function onFolder() {
  const dir = await browseFolder('Choose a folder of block JSON files')
  if (dir) await runParse({ path: dir })
}

// ---- save ----
async function doSave() {
  const blocks: LibraryBlock[] = []
  for (const r of newRows.value) if (r.sel) blocks.push(toBlock(r, newId('block')))
  for (const r of dupRows.value) if (r.mode === 'replace' && r.existing_id) blocks.push(toBlock(r, r.existing_id))
  if (!blocks.length) return
  busy.value = true
  try {
    const res = await importBlocks(blocks)
    push(res.errors.length ? `Imported ${res.saved}, ${res.errors.length} failed` : `Imported ${res.saved} block${res.saved !== 1 ? 's' : ''}`,
      res.errors.length ? 'err' : 'ok')
    emit('done')
  } catch (e) {
    push(e instanceof Error ? e.message : 'Import failed', 'err')
  } finally { busy.value = false }
}
function toBlock(r: Row, id: string): LibraryBlock {
  return { id, category: r.category, name: r.name, text: r.text, polarity: r.polarity, tags: r.tags }
}

function onKey(e: KeyboardEvent) { if (e.key === 'Escape') emit('close') }
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <div class="imp-back" @click="emit('close')" @contextmenu.prevent>
      <div class="imp" @click.stop>
        <div class="imp-hd">
          <span class="ttl">Import blocks</span><div class="sp"></div>
          <button class="x" title="Close (Esc)" @click="emit('close')">✕</button>
        </div>

        <!-- ===== drop / choose ===== -->
        <div v-if="stage === 'drop'" class="imp-body">
          <div class="drop" :class="{ hover: dragOver }" @click="fileInput?.click()"
            @dragenter.prevent="dragOver = true" @dragover.prevent="dragOver = true"
            @dragleave.prevent="dragOver = false" @drop.prevent="onDrop">
            <div class="ic">⭳</div>
            <div class="big">Drag JSON or ZIP files here</div>
            <div class="or">or <span class="browse">click to browse</span></div>
          </div>
          <div class="dropfoot"><div class="rule"></div><span class="or">or</span><div class="rule"></div></div>
          <div class="folder"><button class="btn" @click="onFolder">🗂 Import from a folder…</button></div>
          <div class="accepts">Accepts a block <code>.json</code> (single or a list), a folder of <code>.json</code> files, or a <code>.zip</code> of them.</div>
        </div>

        <!-- ===== review ===== -->
        <div v-else class="imp-body">
          <div class="rvbar">
            <span class="cnt">{{ rows.length }} found · {{ newRows.length }} new<span v-if="dupRows.length"> · {{ dupRows.length }} already exist</span></span>
            <span v-if="skipped.length" class="skip" :title="skipped.map((s) => `${s.source}: ${s.error}`).join('\n')">{{ skipped.length }} skipped</span>
            <div class="sp"></div>
            <button v-if="newRows.length" class="link" @click="toggleAllNew">{{ allNewSel ? 'Deselect all' : 'Select all new' }}</button>
          </div>

          <div class="scroll">
            <!-- duplicates -->
            <div v-if="dupRows.length" class="grp dup">
              <div class="grphd"><span class="dupbadge">⚠ Already in your library</span><span class="gc">{{ dupRows.length }} block{{ dupRows.length > 1 ? 's' : '' }}</span><div class="sp"></div><span class="gc">skip / replace each</span></div>
              <div v-for="(r, i) in dupRows" :key="'d' + i" class="row dup">
                <div class="rmid">
                  <div class="rname">{{ r.name || '(unnamed)' }}</div>
                  <div class="match">matches existing “{{ r.existing_name || r.existing_id }}”</div>
                </div>
                <div class="seg">
                  <button :class="{ on: r.mode === 'skip' }" @click="r.mode = 'skip'">Skip</button>
                  <button :class="{ on: r.mode === 'replace', rep: r.mode === 'replace' }" @click="r.mode = 'replace'">Replace</button>
                </div>
              </div>
            </div>

            <!-- new, grouped by category -->
            <div v-for="g in newGroups" :key="g.category" class="grp">
              <div class="grphd">
                <span class="cbadge" :style="{ background: catColor(g.category) }">{{ catName(g.category) }}</span>
                <span class="gc">{{ g.items.length }} block{{ g.items.length > 1 ? 's' : '' }}</span>
                <div class="sp"></div>
                <button class="link" @click="toggleGroup(g.items)">Select group</button>
              </div>
              <div v-for="(r, i) in g.items" :key="g.category + i" class="row" :class="{ on: r.sel }" @click="r.sel = !r.sel">
                <span class="cbx" :class="{ on: r.sel }">{{ r.sel ? '✓' : '' }}</span>
                <div class="rmid"><div class="rname">{{ r.name || '(unnamed)' }}</div><div class="rtext">{{ r.text }}</div></div>
                <span class="pol" :class="r.polarity === 'negative' ? 'neg' : 'pos'">{{ r.polarity === 'negative' ? 'neg' : 'pos' }}</span>
                <div class="rtags"><span v-for="t in r.tags.slice(0, 2)" :key="t" class="tag">{{ t }}</span><span v-if="r.tags.length > 2" class="tag">+{{ r.tags.length - 2 }}</span></div>
              </div>
            </div>

            <div v-if="!rows.length" class="allskip">All {{ skipped.length }} file(s) were skipped — nothing to import.</div>
          </div>
        </div>

        <div class="imp-ft">
          <span v-if="stage === 'review'" class="sub">{{ newSel }} new<span v-if="repSel"> · {{ repSel }} replace</span></span>
          <div class="sp"></div>
          <button class="btn" @click="emit('close')">Cancel</button>
          <button class="btn primary" :disabled="busy || saveCount === 0" @click="doSave">
            {{ busy ? 'Working…' : saveCount ? `Save ${saveCount} to library` : 'Save to library' }}
          </button>
        </div>
      </div>
    </div>
    <input ref="fileInput" type="file" accept=".json,.zip" multiple style="display:none" @change="onPick" />
  </Teleport>
</template>

<style scoped>
.imp-back{position:fixed;inset:0;z-index:2200;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:24px}
.imp{width:min(640px,100%);max-height:88vh;background:var(--surface-1);border:1px solid var(--border-strong);border-radius:12px;
  box-shadow:0 24px 60px rgba(0,0,0,.5);display:flex;flex-direction:column;overflow:hidden}
.imp-hd{display:flex;align-items:center;gap:10px;padding:15px 20px;border-bottom:1px solid var(--border);flex-shrink:0}
.imp-hd .ttl{font-size:15px;font-weight:650}.imp-hd .sp{flex:1}
.imp-hd .x{width:28px;height:28px;border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);cursor:pointer}
.imp-hd .x:hover{color:var(--text);border-color:var(--border-strong)}
.imp-body{padding:20px;overflow:auto;flex:1;min-height:0}
.imp-ft{display:flex;align-items:center;gap:10px;padding:14px 20px;border-top:1px solid var(--border);flex-shrink:0}
.imp-ft .sp{flex:1}.imp-ft .sub{font-size:12px;color:var(--text-faint)}

.btn{border-radius:var(--radius);font:inherit;font-size:13px;font-weight:600;padding:8px 14px;cursor:pointer;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text-dim)}
.btn:hover{color:var(--text)}
.btn.primary{border:0;background:var(--accent);color:var(--on-accent)}
.btn.primary:disabled{opacity:.5;cursor:default}
.link{border:0;background:transparent;color:var(--accent);font:inherit;font-size:12px;font-weight:600;cursor:pointer;padding:0}

.drop{border:2px dashed var(--border-strong);border-radius:var(--radius-lg);background:var(--surface-2);padding:40px 24px;text-align:center;display:flex;flex-direction:column;align-items:center;gap:8px;cursor:pointer}
.drop.hover{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 8%,var(--surface-2))}
.drop .ic{font-size:30px;color:var(--text-faint)}
.drop .big{font-size:14px;font-weight:600;color:var(--text)}
.drop .or{font-size:12px;color:var(--text-faint)}
.drop .browse{color:var(--accent);font-weight:600;text-decoration:underline;text-underline-offset:2px}
.dropfoot{display:flex;align-items:center;gap:10px;margin-top:14px}
.dropfoot .rule{flex:1;height:1px;background:var(--border)}
.dropfoot .or{font-size:11px;color:var(--text-faint);text-transform:uppercase;letter-spacing:.4px}
.folder{display:flex;justify-content:center;margin-top:14px}
.accepts{font-size:11.5px;color:var(--text-faint);margin-top:16px;text-align:center;line-height:1.6}
.accepts code{background:var(--surface-3);border:1px solid var(--border);border-radius:4px;padding:0 5px;color:var(--text-dim)}

.rvbar{display:flex;align-items:center;gap:10px;margin-bottom:12px;font-size:12px}
.rvbar .cnt{font-weight:600;color:var(--text-dim)}.rvbar .sp{flex:1}
.rvbar .skip{font-size:11px;font-weight:600;color:var(--warn,#b65c02);border:1px solid color-mix(in srgb,var(--warn,#b65c02) 45%,var(--border));border-radius:10px;padding:1px 8px;cursor:help}
.scroll{display:flex;flex-direction:column;gap:14px}
.grp{display:flex;flex-direction:column;gap:6px}
.grphd{display:flex;align-items:center;gap:8px;padding:2px}
.grphd .sp{flex:1}.grphd .gc{font-size:11px;color:var(--text-faint)}
.cbadge{font-size:10px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;color:#fff;border-radius:10px;padding:1px 8px}
.row{display:flex;align-items:center;gap:10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface-2);padding:9px 11px;cursor:pointer}
.row.on{border-color:color-mix(in srgb,var(--accent) 45%,var(--border));background:color-mix(in srgb,var(--accent) 7%,var(--surface-2))}
.cbx{width:17px;height:17px;flex-shrink:0;border-radius:5px;border:1.5px solid var(--border-strong);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:800;color:#fff}
.cbx.on{background:var(--accent);border-color:var(--accent)}
.rmid{flex:1;min-width:0}
.rname{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rtext{font-size:11.5px;color:var(--text-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.pol{font-size:9px;font-weight:800;letter-spacing:.3px;text-transform:uppercase;border-radius:9px;padding:1px 7px;flex-shrink:0}
.pol.pos{color:var(--accent);border:1px solid color-mix(in srgb,var(--accent) 40%,var(--border))}
.pol.neg{color:var(--danger,#e2483d);border:1px solid color-mix(in srgb,var(--danger,#e2483d) 40%,var(--border))}
.rtags{display:flex;gap:4px;flex-shrink:0}
.tag{font-size:10px;color:var(--text-dim);background:var(--surface-3);border:1px solid var(--border);border-radius:9px;padding:0 6px}

.grp.dup .grphd{border-bottom:1px dashed color-mix(in srgb,var(--warn,#b65c02) 40%,var(--border));padding-bottom:6px}
.dupbadge{font-size:10px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;color:var(--warn,#b65c02);border:1px solid color-mix(in srgb,var(--warn,#b65c02) 45%,var(--border));border-radius:10px;padding:1px 8px}
.row.dup{border-color:color-mix(in srgb,var(--warn,#b65c02) 30%,var(--border));background:color-mix(in srgb,var(--warn,#b65c02) 6%,var(--surface-2));cursor:default}
.row .match{font-size:11px;color:var(--warn,#b65c02);margin-top:1px}
.seg{display:flex;flex-shrink:0;border:1px solid var(--border-strong);border-radius:var(--radius);overflow:hidden}
.seg button{border:0;background:transparent;color:var(--text-dim);font:inherit;font-size:11.5px;font-weight:600;padding:5px 11px;cursor:pointer}
.seg button.on{background:var(--accent);color:var(--on-accent)}
.seg button.on.rep{background:var(--warn,#b65c02)}
.allskip{color:var(--text-faint);font-size:13px;text-align:center;padding:20px}
</style>
