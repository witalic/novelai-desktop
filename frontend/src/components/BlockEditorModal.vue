<script setup lang="ts">
// Shared block editor — a centered modal used both from the Library grid and, in-place, from the
// canvas station's "Save to Library". Self-contained: loads its own categories/tags/examples,
// owns a create-category sub-modal, and saves/deletes via the API. Emits the saved block so a
// caller (e.g. the canvas) can link a pin without a round-trip through the Library tab.
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { deleteBlock, listCategories, listExamples, listTags, saveBlock, saveCategory, type ExampleImage } from '../api'
import { useToast } from '../composables/useToast'
import { useConfirm } from '../composables/useConfirm'
import type { CategoryCount, LibraryBlock, TagCount } from '../types'

const props = defineProps<{ block: LibraryBlock; isNew: boolean }>()
const emit = defineEmits<{ close: []; saved: [LibraryBlock]; deleted: [string] }>()
const { push } = useToast()
const { confirm } = useConfirm()

const PALETTE = ['#6e5dc6', '#0c66e4', '#ae4787', '#1f845a', '#b65c02', '#12b5a6', '#d4537e', '#e2483d', '#2fb8c6', '#738496']

// Local editable copy so Cancel discards; the caller's block object is never mutated until save.
const block = ref<LibraryBlock>({ ...props.block, tags: [...props.block.tags] })

const categories = ref<CategoryCount[]>([])
const catColor = (slug: string) => categories.value.find((c) => c.slug === slug)?.color || '#738496'
const catName = (slug: string) => categories.value.find((c) => c.slug === slug)?.name || slug
async function loadCategories() { try { categories.value = await listCategories() } catch { /* keep prior */ } }

// ---- tags ----
const allTags = ref<TagCount[]>([])
const edTagInput = ref('')
const tagFocus = ref(false)
const edTagMatches = computed(() => {
  const q = edTagInput.value.trim().toLowerCase()
  const chosen = new Set(block.value.tags)
  return allTags.value.filter((t) => !chosen.has(t.name) && (!q || t.name.toLowerCase().includes(q)))
})
function addEdTag(name: string) {
  const n = name.trim()
  if (!n) return
  if (!block.value.tags.includes(n)) block.value.tags.push(n)
  edTagInput.value = ''
}
function removeEdTag(name: string) { block.value.tags = block.value.tags.filter((t) => t !== name) }

// ---- example images (generated images sharing the block's tags) ----
const examples = ref<ExampleImage[]>([])
const showExamples = ref(true)
const lightbox = ref<string | null>(null)
let exTimer: ReturnType<typeof setTimeout> | null = null
async function loadExamples() {
  if (!block.value.tags.length) { examples.value = []; return }
  try { examples.value = await listExamples(block.value.tags, 12) } catch { examples.value = [] }
}
watch(() => block.value.tags, () => {
  if (exTimer) clearTimeout(exTimer)
  exTimer = setTimeout(loadExamples, 250)
}, { deep: true })

onMounted(async () => {
  document.addEventListener('mousedown', onDocPointer)
  await loadCategories()
  allTags.value = await listTags('')
  if (block.value.tags.length) loadExamples()
})
onUnmounted(() => {
  document.removeEventListener('mousedown', onDocPointer)
  if (exTimer) clearTimeout(exTimer)
})

// ---- category selector (fixed dropdown, measured off the button so the modal can't clip it) ----
const blockCatOpen = ref(false)
const blockCatEl = ref<HTMLElement | null>(null)
const blockCatStyle = ref<Record<string, string>>({})
function toggleBlockCat(e: MouseEvent) {
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
  blockCatStyle.value = { left: `${r.left}px`, top: `${r.bottom + 4}px`, width: `${r.width}px` }
  blockCatOpen.value = !blockCatOpen.value
}
function chooseBlockCat(slug: string) { block.value.category = slug; blockCatOpen.value = false }
function onDocPointer(e: MouseEvent) {
  if (blockCatEl.value && !blockCatEl.value.contains(e.target as Node)) blockCatOpen.value = false
}

// ---- quick create-category sub-modal (from "New category…") ----
const catModal = ref<{ name: string; color: string } | null>(null)
function openCreateCategory() { blockCatOpen.value = false; catModal.value = { name: '', color: PALETTE[0] } }
function closeCatModal() { catModal.value = null }
async function saveCatModal() {
  const m = catModal.value
  if (!m) return
  if (!m.name.trim()) { push('Category name is required', 'err'); return }
  try {
    const res = await saveCategory(m.name.trim(), m.color)
    await loadCategories()
    catModal.value = null
    block.value.category = res.slug
  } catch (e) { push(e instanceof Error ? e.message : 'Save failed', 'err') }
}

// ---- save / delete ----
const canSave = computed(() => block.value.name.trim().length > 0 && block.value.text.trim().length > 0)
async function save() {
  if (!block.value.name.trim()) { push('Block name is required', 'err'); return }
  if (!block.value.text.trim()) { push('Prompt text is required', 'err'); return }
  try {
    await saveBlock(block.value)
    push(props.isNew ? 'Block created' : 'Block saved', 'ok')
    // A fresh vault block is version 1; an edit keeps the server-owned version (the caller refreshes).
    emit('saved', { ...block.value, tags: [...block.value.tags], version: props.isNew ? 1 : (block.value.version ?? 1) })
  } catch (e) { push(e instanceof Error ? e.message : 'Save failed', 'err') }
}
async function remove() {
  if (!(await confirm({ title: 'Delete block', message: `Delete “${block.value.name || 'block'}”? This can't be undone.`, confirmLabel: 'Delete', danger: true }))) return
  try { await deleteBlock(block.value.id); push('Block deleted', 'ok'); emit('deleted', block.value.id) }
  catch (e) { push(e instanceof Error ? e.message : 'Delete failed', 'err') }
}
</script>

<template>
  <Teleport to="body">
    <div class="edit-back" @click="emit('close')">
      <div class="edit" :class="{ noex: !showExamples }" @click.stop>
        <div class="edit-hd">
          <span class="ttl">{{ isNew ? 'New block' : 'Edit block' }}</span>
          <div class="extoggle">
            <button :class="{ on: showExamples }" @click="showExamples = true">Examples on</button>
            <button :class="{ on: !showExamples }" @click="showExamples = false">off</button>
          </div>
          <button class="x" title="Close" @click="emit('close')">✕</button>
        </div>
        <div class="edit-body">
          <div class="medit">
            <div class="fld"><label>Name</label><input v-model="block.name" placeholder="Block name" /></div>
            <div class="row2">
              <div class="fld"><label>Category</label>
                <div class="catselect" ref="blockCatEl">
                  <button class="catselbtn" @click="toggleBlockCat">
                    <span class="cdot" :style="{ background: catColor(block.category) }"></span>
                    <span class="cn">{{ catName(block.category) }}</span>
                    <span class="chev">▾</span>
                  </button>
                  <div v-if="blockCatOpen" class="catseldrop" :style="blockCatStyle">
                    <div v-for="c in categories" :key="c.slug" class="co" @click="chooseBlockCat(c.slug)">
                      <span class="cdot" :style="{ background: c.color }"></span>{{ c.name }}
                    </div>
                    <div class="co create" @click="openCreateCategory">＋ New category…</div>
                  </div>
                </div>
              </div>
              <div class="fld"><label>Polarity</label>
                <div class="seg">
                  <button :class="{ on: block.polarity === 'positive' }" @click="block.polarity = 'positive'">＋ Positive</button>
                  <button class="neg" :class="{ on: block.polarity === 'negative' }" @click="block.polarity = 'negative'">− Negative</button>
                </div>
              </div>
            </div>

            <div class="fld"><label>Text (prompt tags) <span class="req">· required</span></label><textarea v-model="block.text" placeholder="1girl, silver hair, …"></textarea></div>

            <div class="fld"><label>Tags</label>
              <div class="tagedit">
                <span v-for="t in block.tags" :key="t" class="et">{{ t }} <b @click="removeEdTag(t)">✕</b></span>
                <input class="ti" v-model="edTagInput" placeholder="Search or add…"
                  @focus="tagFocus = true" @blur="tagFocus = false" @keyup.enter="addEdTag(edTagInput)" />
              </div>
              <div v-if="tagFocus && (edTagMatches.length || edTagInput.trim())" class="accd">
                <div v-for="t in edTagMatches" :key="t.name" class="tsopt" @mousedown.prevent="addEdTag(t.name)">
                  <span class="cn">{{ t.name }}</span><span class="cc">{{ t.count }}</span>
                </div>
                <div v-if="edTagInput.trim() && !allTags.some((t) => t.name === edTagInput.trim())" class="tsopt create" @mousedown.prevent="addEdTag(edTagInput)">
                  <span class="cn">＋ Create “{{ edTagInput.trim() }}”</span>
                </div>
              </div>
            </div>
          </div>

          <div v-if="showExamples" class="mexamples">
            <div class="exlbl">Examples — images sharing these tags</div>
            <div v-if="examples.length" class="examples">
              <img v-for="ex in examples" :key="ex.image_id" :src="`${ex.url}?w=400`" alt="example" loading="lazy"
                title="Click to enlarge" @click="lightbox = ex.url" />
            </div>
            <div v-else class="prev"><b>Inherited by images</b>Images generated with this block carry its tags automatically — the most-matching ones show up here.</div>
          </div>
        </div>
        <div class="edit-ft">
          <button v-if="!isNew" class="del" @click="remove">🗑 Delete</button>
          <span class="sp"></span>
          <button class="cancel" @click="emit('close')">Cancel</button>
          <button class="save" :disabled="!canSave" @click="save">Save block</button>
        </div>
      </div>
    </div>

    <!-- quick create-category sub-modal -->
    <div v-if="catModal" class="catmodal-back" @click="closeCatModal">
      <div class="catmodal" @click.stop>
        <div class="cmhd">New category</div>
        <div class="fld"><label>Name</label>
          <input class="nameinput" v-model="catModal.name" placeholder="Category name" @keyup.enter="saveCatModal" />
        </div>
        <div class="fld"><label>Color</label>
          <div class="cmswatches">
            <span v-for="col in PALETTE" :key="col" class="sw" :class="{ on: catModal.color.toLowerCase() === col }"
              :style="{ background: col }" @click="catModal.color = col"></span>
            <label class="hexpick">
              <input type="color" v-model="catModal.color" />
              <span class="hexval">{{ catModal.color.toUpperCase() }}</span>
            </label>
          </div>
        </div>
        <div class="cmfoot">
          <span class="sp"></span>
          <button class="cancel" @click="closeCatModal">Cancel</button>
          <button class="save" @click="saveCatModal">Create</button>
        </div>
      </div>
    </div>

    <!-- example image lightbox -->
    <div v-if="lightbox" class="lightbox" @click="lightbox = null">
      <img :src="lightbox" alt="example" />
    </div>
  </Teleport>
</template>

<style scoped>
.edit-back{position:fixed;inset:0;z-index:1600;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:24px}
.edit{width:min(760px,96vw);max-height:88vh;display:flex;flex-direction:column;border:1px solid var(--border-strong);border-radius:12px;background:var(--surface-1);box-shadow:0 20px 60px rgba(0,0,0,.5);overflow:hidden}
.edit.noex{width:min(520px,96vw)}
.edit-hd{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid var(--border);font-size:15px;font-weight:700}
.edit-hd .ttl{margin-right:auto}
.edit-hd .extoggle{display:inline-flex;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;font-size:11.5px}
.edit-hd .extoggle button{border:0;border-left:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);font:inherit;font-weight:600;padding:4px 10px;cursor:pointer}
.edit-hd .extoggle button:first-child{border-left:0}
.edit-hd .extoggle button.on{background:var(--nav-active);color:var(--accent)}
.edit-hd .x{border:0;background:transparent;color:var(--text-faint);font-size:16px;cursor:pointer}
.edit-hd .x:hover{color:var(--text)}
.edit-body{flex:1;min-height:0;overflow-y:auto;display:flex}
.medit{flex:1;min-width:0;padding:16px 18px;display:flex;flex-direction:column;gap:13px}
.row2{display:grid;grid-template-columns:1fr 160px;gap:12px}
.mexamples{width:280px;flex-shrink:0;border-left:1px solid var(--border);padding:16px;overflow-y:auto;background:color-mix(in srgb,var(--surface-2) 40%,transparent);display:flex;flex-direction:column}
.mexamples .exlbl{font-size:10.5px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;color:var(--text-faint);margin-bottom:10px;flex-shrink:0}
/* show ~6 examples (3 rows × 2); the rest scroll so the modal never stretches tall */
.mexamples .examples{min-height:0;max-height:500px;overflow-y:auto;padding-right:4px}
.edit-ft{display:flex;align-items:center;gap:8px;padding:12px 18px;border-top:1px solid var(--border)}
.edit-ft .del{color:var(--warn);border:1px solid color-mix(in srgb,var(--warn) 40%,var(--border));background:transparent;border-radius:var(--radius);padding:7px 12px;font-size:12.5px;font-weight:600;cursor:pointer}
.edit-ft .del:hover{border-color:var(--danger);color:var(--danger)}
.edit-ft .sp{flex:1}
.edit-ft .cancel{border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text);border-radius:var(--radius);padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer}
.edit-ft .save{border:0;background:var(--accent);color:var(--on-accent);border-radius:var(--radius);padding:8px 16px;font-size:13px;font-weight:700;cursor:pointer}
.edit-ft .save:disabled{opacity:.5;cursor:default}

.fld{display:flex;flex-direction:column;gap:6px}
.fld label{font-size:11px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;color:var(--text-faint)}
.fld label .req{color:var(--text-faint);font-weight:500}
.fld>input,.fld textarea{width:100%;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px;color:var(--text);font:inherit;font-size:13px;outline:none}
.fld textarea{min-height:92px;resize:vertical;line-height:1.5}
.fld>input:focus,.fld textarea:focus{border-color:var(--accent)}

.seg{display:flex;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.seg button{flex:1;border:0;background:transparent;color:var(--text-dim);font:inherit;font-size:12px;font-weight:600;padding:8px;cursor:pointer}
.seg button.on{background:var(--accent);color:#fff}
.seg button.neg.on{background:#e2483d}

.catselect{position:relative}
.catselbtn{width:100%;display:flex;align-items:center;gap:8px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px;color:var(--text);font:inherit;font-size:13px;cursor:pointer}
.catselbtn:hover{border-color:var(--border-strong)}
.catselbtn .cdot{width:9px;height:9px;border-radius:2px;flex-shrink:0}
.catselbtn .cn{flex:1;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.catselbtn .chev{color:var(--text-faint);font-size:11px}
.catseldrop{position:fixed;z-index:1650;background:var(--surface-2);border:1px solid var(--border-strong);border-radius:var(--radius);overflow:hidden;max-height:280px;overflow-y:auto;box-shadow:0 8px 24px rgba(0,0,0,.45)}
.catseldrop .co{display:flex;align-items:center;gap:8px;padding:8px 10px;font-size:12.5px;color:var(--text-dim);cursor:pointer}
.catseldrop .co:hover{background:var(--surface-3)}
.catseldrop .co .cdot{width:9px;height:9px;border-radius:2px;flex-shrink:0}
.catseldrop .co.create{color:var(--accent);font-weight:600;border-top:1px dashed var(--border)}

.tagedit{display:flex;flex-wrap:wrap;gap:6px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:7px}
.tagedit .ti{flex:1;min-width:80px;border:0;background:transparent;color:var(--text);font:inherit;font-size:12px;outline:none}
.et{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:var(--accent);background:var(--nav-active);border:1px solid color-mix(in srgb,var(--accent) 30%,transparent);border-radius:20px;padding:2px 8px}
.et b{color:var(--text-faint);font-weight:400;cursor:pointer}
.accd{margin-top:5px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;max-height:180px;overflow-y:auto}
.tsopt{display:flex;align-items:center;gap:8px;padding:6px 9px;font-size:12px;color:var(--text-dim);cursor:pointer}
.tsopt:hover{background:var(--surface-3)}
.tsopt .cn{flex:1}
.tsopt .cc{font-size:11px;color:var(--text-faint)}
.tsopt.create{color:var(--accent);font-weight:600;border-top:1px dashed var(--border)}
.examples{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.examples img{width:100%;aspect-ratio:3/4;object-fit:cover;border-radius:7px;border:1px solid var(--border);background:var(--surface-2);cursor:zoom-in;transition:border-color .1s}
.examples img:hover{border-color:var(--accent)}
.prev{background:var(--surface-2);border:1px dashed var(--border-strong);border-radius:var(--radius);padding:9px 11px;font-size:11.5px;color:var(--text-dim);line-height:1.5}
.prev b{color:var(--text-faint);font-weight:700;letter-spacing:.3px;text-transform:uppercase;font-size:10px;display:block;margin-bottom:3px}

/* create-category sub-modal */
.catmodal-back{position:fixed;inset:0;z-index:1700;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:20px}
.catmodal{width:min(360px,100%);background:var(--surface-1);border:1px solid var(--border);border-radius:var(--radius-lg);box-shadow:0 18px 48px rgba(0,0,0,.5);padding:18px 20px;display:flex;flex-direction:column;gap:14px}
.catmodal .cmhd{font-size:15px;font-weight:650;color:var(--text)}
.catmodal .nameinput{width:100%;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px;color:var(--text);font:inherit;font-size:13px;outline:none}
.catmodal .nameinput:focus{border-color:var(--accent)}
.catmodal .cmswatches{display:flex;flex-wrap:wrap;gap:7px;align-items:center}
.catmodal .cmswatches .sw{width:20px;height:20px;border-radius:50%;border:2px solid transparent;cursor:pointer}
.catmodal .cmswatches .sw.on{border-color:var(--text)}
.hexpick{display:inline-flex;align-items:center;gap:6px;margin-left:2px;padding-left:8px;border-left:1px solid var(--border-strong);cursor:pointer}
.hexpick input[type=color]{width:20px;height:20px;padding:0;border:1px solid var(--border-strong);border-radius:4px;background:none;cursor:pointer}
.hexval{font-size:11px;font-weight:600;color:var(--text-dim);font-family:ui-monospace,monospace}
.catmodal .cmfoot{display:flex;align-items:center;gap:8px;margin-top:4px}
.catmodal .cmfoot .sp{flex:1}
.catmodal .cmfoot .cancel{border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer}
.catmodal .cmfoot .save{border:0;background:var(--accent);color:#fff;border-radius:var(--radius);padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer}

.lightbox{position:fixed;inset:0;z-index:2500;background:rgba(0,0,0,.82);display:flex;align-items:center;justify-content:center;padding:24px;cursor:zoom-out}
.lightbox img{max-width:min(92vw,900px);max-height:92vh;object-fit:contain;border-radius:8px;box-shadow:0 12px 48px rgba(0,0,0,.6)}
</style>
