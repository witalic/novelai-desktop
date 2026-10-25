/* Agent access status, shared app-wide (backend GET /api/agent): Settings → Agents and Agent activity show it, the
 * sidebar's dot and badge summarise it from any view. Singleton module state, polled every few seconds while the
 * listener runs (only then can agents act) — and until a first read succeeds; Settings re-reads it whenever it
 * opens. `fresh` is the activity the user hasn't looked at yet (since Agent activity was last on screen). */
import { computed, ref } from 'vue'
import { clearAgentActivity, getAgent, patchAgent, regenerateAgentToken, type AgentPatch, type AgentStatus } from '../api'

const POLL_MS = 5000
const status = ref<AgentStatus | null>(null)
const busy = ref(false)
const seen = ref(0) // the newest activity `seq` the user has looked at
let seq = 0
let polling = false

// Responses can land out of order (a poll started before a save, finishing after it): only the newest counts.
function adopt(s: AgentStatus, mine: number) {
  if (mine !== seq) return
  status.value = s
  // A restarted backend counts from 1 again — what the user saw before is gone.
  if ((s.activity[0]?.seq ?? Infinity) < seen.value) seen.value = 0
}
async function load() {
  if (busy.value) return
  const mine = ++seq
  try { adopt(await getAgent(), mine) } catch { /* backend not ready — keep the last status */ }
}
// A change the user made: false when another one is still running (ignored), throws when it fails.
async function change(call: () => Promise<AgentStatus>): Promise<boolean> {
  if (busy.value) return false
  busy.value = true
  const mine = ++seq
  try { adopt(await call(), mine); return true } finally { busy.value = false }
}

const fresh = computed(() => status.value?.activity.filter((a) => a.seq > seen.value) ?? [])
const statusText = computed(() => {
  const s = status.value
  if (!s?.config.enabled) return 'Off'
  if (!s.running) return 'Not running'
  return s.connected ? `${s.connected} agent${s.connected === 1 ? '' : 's'} connected` : 'Waiting for an agent'
})

export function useAgentStatus() {
  if (!polling) {
    polling = true
    load()
    setInterval(() => { if (!status.value || status.value.running) load() }, POLL_MS)
  }
  return {
    status, busy, statusText, load,
    save: (patch: AgentPatch) => change(() => patchAgent(patch)),
    regenerate: () => change(regenerateAgentToken),
    clear: () => change(clearAgentActivity),
    freshCount: computed(() => fresh.value.length),
    freshProblem: computed(() => fresh.value.some((a) => a.outcome !== 'ok')),
    // Reads fold into the newest row, so the first row always carries the highest seq.
    markSeen: () => { seen.value = Math.max(seen.value, status.value?.activity[0]?.seq ?? 0) },
  }
}
