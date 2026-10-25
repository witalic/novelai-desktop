<script setup lang="ts">
/* Settings → Agent activity: everything agents did since the app started (backend app/agent/activity — the last
 * 200 rows, in memory), filtered by kind, grouped by day. While it is on screen the user has seen it all, which
 * clears the sidebar's badge. */
import { computed, ref, watch } from 'vue'
import { ACTIVITY_FILTERS, groupByDay, matchesFilter, type ActivityFilter } from '../agentActivity'
import { useAgentStatus } from '../composables/useAgentStatus'
import { useConfirm } from '../composables/useConfirm'
import { useToast } from '../composables/useToast'
import ActivityRow from './ActivityRow.vue'

const props = defineProps<{ active: boolean }>() // shown right now
const { push } = useToast()
const { confirm } = useConfirm()
const agent = useAgentStatus()

const filter = ref<ActivityFilter>('all')
const rows = computed(() => agent.status.value?.activity ?? [])
const counts = computed(() => Object.fromEntries(
  ACTIVITY_FILTERS.map((f) => [f.id, rows.value.filter((a) => matchesFilter(a, f.id)).length])))
const groups = computed(() => groupByDay(rows.value.filter((a) => matchesFilter(a, filter.value))))

watch([() => props.active, rows], () => { if (props.active) agent.markSeen() }, { immediate: true })

async function clearLog() {
  const ok = await confirm({
    title: 'Clear agent activity',
    message: 'Remove every entry from the log? Agents keep their access — this only empties the list.',
    confirmLabel: 'Clear',
  })
  if (!ok) return
  try { await agent.clear() } catch (e) { push(e instanceof Error ? e.message : 'Could not clear', 'err') }
}
</script>

<template>
  <div class="card log">
    <div class="toolbar">
      <button v-for="f in ACTIVITY_FILTERS" :key="f.id" class="chip" :class="{ on: filter === f.id, bad: f.id === 'problem' }"
        @click="filter = f.id">{{ f.label }}<span class="k">{{ counts[f.id] }}</span></button>
      <span class="sp"></span>
      <button class="gbtn" :disabled="!rows.length || agent.busy.value" @click="clearLog">Clear</button>
    </div>
    <div class="list">
      <template v-for="(g, i) in groups" :key="`${i}-${g.day}`">
        <div class="day">{{ g.day }}</div>
        <ActivityRow v-for="a in g.rows" :key="a.seq" :entry="a" />
      </template>
      <div v-if="!groups.length" class="empty">{{ rows.length ? 'Nothing of this kind yet.'
        : 'No agent activity yet. Everything an agent reads or changes — and every refused call — shows up here.' }}</div>
    </div>
    <div class="card-ft"><span class="hint">Keeps the last <b>200</b> entries until the app restarts · repeated reads within a minute
      share one row (×N)</span></div>
  </div>
</template>

<style scoped>
.log{flex:1;display:flex;flex-direction:column;min-height:0}
.toolbar{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:10px 18px;border-bottom:1px solid var(--border)}
.chip{display:flex;align-items:center;gap:6px;border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);
  border-radius:20px;font-size:12px;font-weight:600;padding:4px 11px;cursor:pointer}
.chip .k{font-weight:700;color:var(--text-faint)}
.chip.on{background:var(--nav-active);color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,var(--border))}
.chip.on .k{color:var(--accent)}
.chip.bad .k{color:var(--danger)}
.list{flex:1;overflow-y:auto;min-height:0}
.day{position:sticky;top:0;z-index:1;background:var(--surface-1);font-size:10.5px;font-weight:700;letter-spacing:.4px;
  text-transform:uppercase;color:var(--text-faint);padding:9px 18px 5px;border-bottom:1px solid var(--border)}
.list .empty{text-align:center;padding:28px 18px}
</style>
