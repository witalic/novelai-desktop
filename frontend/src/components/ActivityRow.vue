<script setup lang="ts">
/* One Agent activity row: when, what (×N for folded reads), and how it ended — denied / failed, or a
 * generation's price. Shared by Settings → Agents (recent) and Agent activity (the full log). */
import type { AgentActivity } from '../api'

defineProps<{ entry: AgentActivity }>()
const timeOf = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
</script>

<template>
  <div class="lrow">
    <span class="t">{{ timeOf(entry.at) }}</span>
    <span class="a"><b>{{ entry.action }}</b><span v-if="entry.count > 1" class="n"> ×{{ entry.count }}</span>
      <template v-if="entry.detail"> — {{ entry.detail }}</template></span>
    <span v-if="entry.outcome !== 'ok'" class="c">{{ entry.outcome === 'denied' ? 'denied' : 'failed' }}</span>
    <span v-else-if="entry.cost != null" class="c" :class="entry.cost ? 'paid' : 'free'">{{ entry.cost ? `◆ ${entry.cost}` : 'free' }}</span>
  </div>
</template>

<style scoped>
.lrow{display:flex;align-items:baseline;gap:12px;padding:7px 18px;font-size:12.5px}
.lrow + .lrow{border-top:1px solid var(--border)}
.t{flex-shrink:0;width:38px;font-variant-numeric:tabular-nums;color:var(--text-faint);font-size:11.5px}
.a{flex:1;min-width:0;color:var(--text-dim)}
.a b{color:var(--text);font-weight:600}
.a .n{font-weight:700;color:var(--text-faint)}
.c{flex-shrink:0;font-size:11px;font-weight:700;color:var(--danger)}
.c.free{color:var(--ok)}
.c.paid{color:var(--accent)}
</style>
