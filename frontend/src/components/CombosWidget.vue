<script setup lang="ts">
/* The Combinations widget (design/combinations-widget.html, Variant A): the work's saved block sets — named,
 * ordered composition blocks (blocks only, no params) kept in the WorkDoc. Add appends a set to the station,
 * Replace swaps the composition for it, ⋯ holds rename / update / duplicate / use-latest / delete; a card
 * dragged onto the station adds it. The list itself is owned by CanvasBoard (prop + emits); this component
 * only renders it and keeps its own view state (search, expanded cards, inline rename). Interactive areas
 * stop node select/drag; empty widget areas still behave as the node. */
import { computed, ref } from 'vue'
import { useContextMenu, type MenuItem } from '../composables/useContextMenu'
import type { PersistedCombo } from '../types'

const props = defineProps<{
  combos: PersistedCombo[]
  selected: boolean
  compCount: number // blocks currently in the station composition (for "Save current composition")
  drift: Record<string, number> // combo id → Library blocks edited since it was saved
  linkedId?: string // the combination the station is linked to (highlighted)
  catColor: (slug: string) => string
  catName: (slug: string) => string
}>()
const emit = defineEmits<{
  'save-current': []
  apply: [{ id: string; mode: 'add' | 'replace' }]
  rename: [{ id: string; name: string }]
  update: [string] // overwrite with the current composition
  duplicate: [string]
  'use-latest': [string]
  delete: [string]
}>()

const search = ref('')
const shown = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return props.combos
  return props.combos.filter((c) => c.name.toLowerCase().includes(q)
    || c.blocks.some((b) => b.name.toLowerCase().includes(q) || b.text.toLowerCase().includes(q)))
})

const expanded = ref<Set<string>>(new Set())
function toggleExpand(id: string) {
  const next = new Set(expanded.value)
  next.has(id) ? next.delete(id) : next.add(id)
  expanded.value = next
}

const negCount = (c: PersistedCombo) => c.blocks.filter((b) => b.polarity === 'negative').length
const preview = (c: PersistedCombo) => c.blocks.filter((b) => b.polarity !== 'negative' && !b.frozen).map((b) => b.text).join(', ')

// Inline rename (⋯ → Rename, or double-click the name).
const renamingId = ref<string | null>(null)
const renameText = ref('')
function startRename(c: PersistedCombo) {
  renamingId.value = c.id
  renameText.value = c.name
  requestAnimationFrame(() => {
    const el = document.getElementById(`combo-name-${c.id}`) as HTMLInputElement | null
    el?.focus(); el?.select()
  })
}
function commitRename() {
  const id = renamingId.value
  renamingId.value = null
  const name = renameText.value.trim()
  const c = props.combos.find((x) => x.id === id)
  if (id && c && name && name !== c.name) emit('rename', { id, name })
}

const { open: openMenu } = useContextMenu()
function onMore(e: MouseEvent, c: PersistedCombo) {
  const items: MenuItem[] = [
    { label: 'Rename', icon: '✎', onClick: () => startRename(c) },
    { label: 'Update from composition', icon: '↻', onClick: () => emit('update', c.id) },
    { label: 'Duplicate', icon: '⧉', onClick: () => emit('duplicate', c.id) },
  ]
  if (props.drift[c.id]) items.push({ label: 'Use latest Library versions', icon: '⇣', onClick: () => emit('use-latest', c.id) })
  items.push({ label: 'Delete', icon: '🗑', danger: true, onClick: () => emit('delete', c.id) })
  openMenu(e, items)
}

function onCardDragStart(e: DragEvent, c: PersistedCombo) {
  if (!e.dataTransfer) return
  e.dataTransfer.effectAllowed = 'copy'
  e.dataTransfer.setData('text/plain', `nai-combo:${c.id}`)
}

// Presses on interactive elements (and on cards, which drag via HTML5 DnD) must not select/drag the zone.
function stopIfInteractive(e: Event) {
  const t = e.target as HTMLElement | null
  if (t && t !== e.currentTarget && t.closest('button, input, .ccard')) e.stopPropagation()
}
</script>

<template>
  <div class="cwidget" :class="{ selected }">
    <div class="cwhd">
      <span class="cicon">◈</span>
      <span class="ctitle">Combinations</span>
      <span class="ccount">{{ combos.length }}</span>
    </div>

    <div class="cwbody nodrag" @pointerdown="stopIfInteractive" @mousedown="stopIfInteractive" @click="stopIfInteractive">
      <div class="cwhead">
        <div class="cwsearch">
          <span class="ic">⌕</span>
          <input v-model="search" type="text" placeholder="Search combinations…" />
        </div>
      </div>

      <div class="cwlist nowheel">
        <button class="newrow" :disabled="!compCount" :title="compCount ? 'Save the station’s blocks as a new combination' : 'The composition is empty'"
          @click="emit('save-current')">＋ Save current composition <b>({{ compCount }} block{{ compCount === 1 ? '' : 's' }})</b></button>

        <div v-if="!combos.length" class="wempty">
          No combinations in this work yet.<br /><br />
          <b>Save as combination</b> on the station, or <b>Save recipe as combination</b> on any gallery image.
        </div>
        <div v-else-if="!shown.length" class="wempty">No combinations match.<br /><button @click="search = ''">Clear search</button></div>

        <div v-for="c in shown" :key="c.id" class="ccard" :class="{ agent: c.origin === 'agent', linked: c.id === linkedId }"
          draggable="true" @dragstart="onCardDragStart($event, c)">
          <div class="r1">
            <span class="grip" title="Drag onto the station to add">⠿</span>
            <button class="chev" :title="expanded.has(c.id) ? 'Collapse' : 'Show blocks'" @click="toggleExpand(c.id)">{{ expanded.has(c.id) ? '▾' : '▸' }}</button>
            <input v-if="renamingId === c.id" :id="`combo-name-${c.id}`" v-model="renameText" class="cnamein"
              @blur="commitRename" @keyup.enter="commitRename" @keyup.esc="renamingId = null" />
            <span v-else class="cname" title="Double-click to rename" @dblclick="startRename(c)">{{ c.name }}</span>
            <span class="sp"></span>
            <button class="btn" title="Append these blocks to the composition" @click="emit('apply', { id: c.id, mode: 'add' })">Add</button>
            <button class="btn pri" title="Replace the composition with these blocks" @click="emit('apply', { id: c.id, mode: 'replace' })">Replace</button>
            <button class="btn more" title="More: rename, update, duplicate, delete" @click="onMore($event, c)">⋯</button>
          </div>
          <div class="meta">
            <span class="dots"><i v-for="(b, i) in c.blocks.slice(0, 14)" :key="i" :style="{ background: catColor(b.category || 'custom') }"></i></span>
            <span>{{ c.blocks.length }} block{{ c.blocks.length === 1 ? '' : 's' }}</span>
            <span v-if="negCount(c)" class="badge neg">{{ negCount(c) }} NEG</span>
            <span v-if="c.origin === 'agent'" class="badge agent" title="Created by an agent via MCP">BY AGENT</span>
            <span v-if="drift[c.id]" class="badge drift" :title="`${drift[c.id]} Library block${drift[c.id] === 1 ? ' was' : 's were'} edited after this combination was saved`">
              {{ drift[c.id] }} CHANGED IN LIBRARY</span>
          </div>
          <div v-if="!expanded.has(c.id)" class="prev">{{ preview(c) || '—' }}</div>
          <template v-else>
            <div class="blist">
              <div v-for="(b, i) in c.blocks" :key="i" class="bl" :class="{ n: b.polarity === 'negative', f: b.frozen }">
                <span class="d" :style="{ background: catColor(b.category || 'custom') }"></span>
                <span class="nm" :title="b.text">{{ b.name || b.text || 'Untitled' }}</span>
                <span class="cat">{{ b.frozen ? '❄ frozen' : catName(b.category || 'custom') }}</span>
              </div>
            </div>
            <div class="cardfoot">
              <button class="btn" title="Overwrite this combination with the current composition" @click="emit('update', c.id)">Update from composition</button>
              <button class="btn" @click="emit('duplicate', c.id)">Duplicate</button>
              <button class="btn del" @click="emit('delete', c.id)">Delete</button>
            </div>
          </template>
        </div>
      </div>
    </div>

    <div class="cwfoot">Saved in this work · drag a card onto the station to add</div>
  </div>
</template>

<style scoped>
.cwidget{position:relative;width:100%;height:100%;display:flex;flex-direction:column;border:1.5px solid var(--border-strong);
  border-radius:12px;overflow:hidden;background:color-mix(in srgb,var(--surface-1) 92%,transparent)}
.cwidget.selected{border-color:var(--accent)}
.cwhd{display:flex;align-items:center;gap:8px;height:38px;flex-shrink:0;padding:0 8px 0 12px;
  border-bottom:1px solid var(--border);background:var(--surface-1);font-weight:600;font-size:13px;cursor:grab}
.ctitle{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ccount{font-size:11px;font-weight:600;color:var(--text-faint)}

.cwbody{display:flex;flex-direction:column;flex:1;min-height:0}
.cwhead{padding:9px 8px;border-bottom:1px solid var(--border);flex-shrink:0}
.cwsearch{position:relative}
.cwsearch input{width:100%;font:inherit;font-size:12px;color:var(--text);background:var(--surface-2);
  border:1px solid var(--border);border-radius:var(--radius);padding:7px 9px 7px 28px;outline:none}
.cwsearch input:focus{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 30%,transparent)}
.cwsearch .ic{position:absolute;left:9px;top:50%;transform:translateY(-50%);color:var(--text-faint);font-size:12px;pointer-events:none}

.cwlist{flex:1;min-height:0;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:7px}
.newrow{flex-shrink:0;display:flex;align-items:center;justify-content:center;gap:6px;width:100%;
  border:1px dashed var(--border-strong);border-radius:8px;background:transparent;color:var(--text-dim);
  font:inherit;font-size:11.5px;font-weight:600;padding:8px;cursor:pointer}
.newrow:hover:not(:disabled){color:var(--accent);border-color:var(--accent);background:color-mix(in srgb,var(--accent) 6%,transparent)}
.newrow:disabled{opacity:.5;cursor:default}
.newrow b{color:var(--text)}

.ccard{flex-shrink:0;border:1px solid var(--border);border-left:4px solid var(--accent);border-radius:8px;background:var(--surface-2);
  padding:7px 8px 8px;cursor:grab}
.ccard:hover{border-color:var(--border-strong);border-left-color:var(--accent)}
.ccard.agent,.ccard.agent:hover{border-left-color:var(--agent)}
.ccard.linked{box-shadow:0 0 0 1px color-mix(in srgb,var(--accent) 45%,transparent)}
.r1{display:flex;align-items:center;gap:6px;min-height:24px}
.grip{color:var(--text-faint);font-size:10px;flex-shrink:0}
.chev{flex-shrink:0;border:0;background:transparent;color:var(--text-faint);cursor:pointer;width:14px;padding:0;font-size:11px}
.chev:hover{color:var(--text)}
.cname{font-size:12.5px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cnamein{flex:1;min-width:0;font:inherit;font-size:12.5px;font-weight:650;color:var(--text);background:var(--surface-1);
  border:1px solid var(--accent);border-radius:5px;padding:2px 6px;outline:none}
.sp{margin-left:auto}
.btn{flex-shrink:0;border:1px solid var(--border-strong);background:var(--surface-1);color:var(--text-dim);border-radius:5px;height:24px;
  font:inherit;font-size:11px;font-weight:700;padding:0 8px;cursor:pointer;display:inline-flex;align-items:center;gap:4px}
.btn:hover{color:var(--accent);border-color:var(--accent)}
.btn.pri{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.btn.pri:hover{background:var(--accent-strong);color:var(--on-accent)}
.btn.more{width:24px;justify-content:center;padding:0}
.btn.del{margin-left:auto}
.btn.del:hover{color:var(--danger);border-color:var(--danger)}
.meta{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-top:4px;font-size:10.5px;color:var(--text-faint)}
.dots{display:inline-flex;gap:2px}
.dots i{width:7px;height:7px;border-radius:50%;display:inline-block}
.badge{font-size:9px;font-weight:800;letter-spacing:.3px;border-radius:9px;padding:0 6px;line-height:15px;border:1px solid}
.badge.agent{color:var(--agent-fg);border-color:color-mix(in srgb,var(--agent) 55%,var(--border));background:color-mix(in srgb,var(--agent) 14%,transparent)}
.badge.drift{color:var(--warn-fg);border-color:color-mix(in srgb,var(--warn) 50%,var(--border));background:color-mix(in srgb,var(--warn) 12%,transparent)}
.badge.neg{color:var(--danger);border-color:color-mix(in srgb,var(--danger) 50%,var(--border));background:color-mix(in srgb,var(--danger) 12%,transparent)}
.prev{font-size:11px;color:var(--text-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:3px}

.blist{margin-top:7px;display:flex;flex-direction:column;gap:3px;border-top:1px solid var(--border);padding-top:7px}
.bl{display:flex;align-items:center;gap:7px;font-size:11.5px;padding:3px 6px;border-radius:5px;
  background:color-mix(in srgb,var(--surface-1) 60%,transparent);border-left:3px solid var(--ok)}
.bl.n{border-left-color:var(--danger)}
.bl.f{border-left-color:var(--border-strong);opacity:.6}
.bl .d{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.bl .nm{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bl .cat{margin-left:auto;font-size:10px;color:var(--text-faint);white-space:nowrap;text-transform:capitalize}
.cardfoot{display:flex;gap:6px;margin-top:8px;padding-top:7px;border-top:1px solid var(--border)}

.wempty{padding:22px 14px;text-align:center;font-size:12px;color:var(--text-faint)}
.wempty b{color:var(--text-dim)}
.wempty button{border:0;background:transparent;color:var(--accent);cursor:pointer;font-size:12px;padding:0}

.cwfoot{flex-shrink:0;display:flex;align-items:center;gap:8px;border-top:1px solid var(--border);
  background:var(--surface-1);padding:7px 12px;font-size:11px;color:var(--text-faint)}
</style>
