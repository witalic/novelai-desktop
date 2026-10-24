<script setup lang="ts">
/* Settings → Agent access (design/settings-agent-access.html): the switch for the app's MCP endpoint, how to
 * connect an agent (address, token, a ready `claude mcp add` command), per-action permissions, and what agents
 * did. The port is the app's own business (owner decision) — the card only shows the address in use. Polls
 * the status while the page is open and access is on. */
import { computed, onActivated, onDeactivated, onUnmounted, ref } from 'vue'
import {
  getAgent, patchAgent, regenerateAgentToken, revealAgentToken, type AgentPatch, type AgentPermissions, type AgentStatus,
} from '../api'
import { copyText } from '../clipboard'
import { useConfirm } from '../composables/useConfirm'
import { useToast } from '../composables/useToast'

const { push } = useToast()
const { confirm } = useConfirm()

const st = ref<AgentStatus | null>(null)
const busy = ref(false)

const PERMS: { group: string; items: { key: keyof AgentPermissions; name: string; desc: string }[] }[] = [
  { group: 'Read', items: [
    { key: 'read', name: 'Browse the Library and works', desc: 'blocks, categories, tags, works, image metadata and images, models' },
  ] },
]

const cfg = computed(() => st.value?.config)
const maskedToken = computed(() => (st.value?.token_hint ? `••••••••••••••••••••${st.value.token_hint}` : 'issued when you switch access on'))
// --scope user: available in every project, kept in the user's own config — never a committable .mcp.json.
const command = (token: string) =>
  `claude mcp add --scope user --transport http novelai ${st.value?.address} --header "Authorization: Bearer ${token}"`
const statusText = computed(() => {
  if (!st.value?.config.enabled) return 'Off'
  if (!st.value.running) return 'Not running'
  const n = st.value.connected
  return n ? `${n} agent${n === 1 ? '' : 's'} connected` : 'Waiting for an agent'
})

// Responses can land out of order (a poll started before a save, finishing after it): only the newest counts.
let seq = 0
function adopt(s: AgentStatus, mine: number) {
  if (mine === seq) st.value = s
}
async function load() {
  if (busy.value) return
  const mine = ++seq
  try { adopt(await getAgent(), mine) } catch { /* backend not ready — keep the last status */ }
}
async function save(patch: AgentPatch) {
  if (busy.value) return
  busy.value = true
  const before = st.value?.error
  const mine = ++seq
  try {
    adopt(await patchAgent(patch), mine)
    if (st.value?.error && st.value.error !== before) push(st.value.error, 'err')
  } catch (e) {
    push(e instanceof Error ? e.message : 'Could not save', 'err') // nothing changed — the card still shows the truth
  } finally { busy.value = false }
}
const setPerm = (key: keyof AgentPermissions, on: boolean) => save({ permissions: { [key]: on } })

async function copy(what: 'address' | 'token' | 'command') {
  try {
    const text = what === 'address' ? st.value?.address ?? '' : what === 'token' ? await revealAgentToken() : command(await revealAgentToken())
    if (await copyText(text)) push(what === 'command' ? 'Command copied — paste it into a terminal' : `${what === 'token' ? 'Token' : 'Address'} copied`, 'ok')
    else push('Could not copy', 'err')
  } catch (e) { push(e instanceof Error ? e.message : 'Could not copy', 'err') }
}
async function regenerate() {
  const ok = await confirm({
    title: 'Regenerate agent token',
    message: 'Issue a new agent token? Every connected agent stops working until you update its config with the new one.',
    confirmLabel: 'Regenerate', danger: true,
  })
  if (!ok) return
  const mine = ++seq
  try { adopt(await regenerateAgentToken(), mine); push('New agent token issued', 'ok') }
  catch (e) { push(e instanceof Error ? e.message : 'Could not regenerate', 'err') }
}

const timeOf = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

// Live status (connections, activity) while the page is visible. Settings lives under KeepAlive, where
// onActivated also fires on the first mount.
let poll: ReturnType<typeof setInterval> | null = null
function startPoll() { stopPoll(); poll = setInterval(() => { if (st.value?.config.enabled) load() }, 5000) }
function stopPoll() { if (poll) clearInterval(poll); poll = null }
onActivated(() => { load(); startPoll() })
onDeactivated(stopPoll)
onUnmounted(stopPoll)
</script>

<template>
  <div class="card">
    <div class="card-hd">Agent access (MCP)<span class="sp"></span>
      <span class="status" :class="{ on: cfg?.enabled && st?.running, bad: cfg?.enabled && !st?.running }"><i></i>{{ statusText }}</span>
    </div>

    <div class="setting">
      <div class="txt"><div class="name">Allow agents to connect</div>
        <div class="desc">Lets AI agents running on this computer (Claude Code, …) work with your vault through MCP while the app
          is open. Loopback only — never reachable from other machines.</div></div>
      <button class="switch" :class="{ on: cfg?.enabled }" role="switch" :aria-checked="!!cfg?.enabled" :disabled="!cfg || busy"
        title="Allow agents to connect" @click="save({ enabled: !cfg?.enabled })"><span class="knob"></span></button>
    </div>

    <div v-show="cfg?.enabled" class="setting col">
      <div class="txt"><div class="name">Connection</div>
        <div class="desc">A fixed address so agents find the app across restarts. The token is a separate key just for agents,
          kept in the OS keychain — not your NovelAI token.</div></div>
      <div v-if="st?.error" class="err">{{ st.error }}</div>
      <div v-if="st?.note" class="note">{{ st.note }}</div>
      <div class="kv">
        <span class="k">Address</span><span class="mono">{{ st?.address }}</span>
        <button class="gbtn" @click="copy('address')">Copy</button>
        <span class="k">Agent token</span><span class="mono">{{ maskedToken }}</span>
        <div class="btnrow"><button class="gbtn" :disabled="!st?.token_hint" @click="copy('token')">Copy</button>
          <button class="gbtn" :disabled="!st?.token_hint" @click="regenerate">Regenerate</button></div>
      </div>
      <div class="cmd">
        <div class="mono">{{ command(st?.token_hint ? `••••${st.token_hint}` : '…') }}</div>
        <button class="gbtn" :disabled="!st?.token_hint" @click="copy('command')">Copy</button>
      </div>
      <div class="hint">Paste into a terminal to connect <b>Claude Code</b> (the copy carries the real token). Regenerating the
        token disconnects every agent until it is updated.</div>
    </div>

    <div class="perm">
      <div class="txt"><div class="name">What agents may do</div>
        <div class="desc">Each switch takes effect on the agent's next call; a refused call shows up in the activity below.</div></div>
      <template v-for="g in PERMS" :key="g.group">
        <div class="pgrp">{{ g.group }}</div>
        <div v-for="p in g.items" :key="p.key" class="prow">
          <div><div class="pn">{{ p.name }}</div><div class="pd">{{ p.desc }}</div></div>
          <span class="sp"></span>
          <button class="switch" :class="{ on: cfg?.permissions[p.key] }" role="switch" :aria-checked="!!cfg?.permissions[p.key]"
            :disabled="!cfg || busy" :title="p.name" @click="setPerm(p.key, !cfg?.permissions[p.key])"><span class="knob"></span></button>
        </div>
      </template>
    </div>
  </div>

  <div class="card">
    <div class="card-hd">Agent activity<span class="sp"></span><span class="hint">since the app started · newest first</span></div>
    <div v-if="!st?.activity.length" class="empty">No agent activity yet. Everything an agent reads — and every refused call —
      shows up here.</div>
    <div v-else class="log">
      <div v-for="(a, i) in st.activity" :key="i" class="lrow">
        <span class="t">{{ timeOf(a.at) }}</span>
        <span class="a"><b>{{ a.action }}</b><span v-if="a.count > 1" class="n"> ×{{ a.count }}</span>
          <template v-if="a.detail"> — {{ a.detail }}</template></span>
        <span v-if="a.outcome !== 'ok'" class="c">{{ a.outcome === 'denied' ? 'denied' : 'failed' }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.card{max-width:620px;background:var(--surface-1);border:1px solid var(--border);border-radius:var(--radius-lg)}
.card-hd{display:flex;align-items:center;gap:8px;padding:14px 18px;border-bottom:1px solid var(--border);font-weight:600}
.sp{flex:1}
.setting{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px 18px}
.setting + .setting,.perm + .setting,.setting + .perm{border-top:1px solid var(--border)}
.setting.col{flex-direction:column;align-items:stretch;gap:12px}
.txt .name{font-weight:500}
.txt .desc{font-size:12px;color:var(--text-dim);margin-top:2px}
.hint{font-size:11.5px;color:var(--text-faint)}
.hint b{color:var(--text-dim)}
.err{font-size:12px;font-weight:600;color:var(--danger)}

.status{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:600;color:var(--text-faint)}
.status i{width:7px;height:7px;border-radius:50%;background:var(--text-faint)}
.status.on{color:var(--ok)}.status.on i{background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 25%,transparent)}
.status.bad{color:var(--danger)}.status.bad i{background:var(--danger)}

.switch{flex-shrink:0;width:36px;height:20px;border-radius:20px;border:1px solid var(--border-strong);background:var(--surface-3);
  position:relative;cursor:pointer;padding:0;transition:background .15s,border-color .15s}
.switch .knob{position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;background:var(--text-faint);transition:left .15s,background .15s}
.switch.on{background:var(--accent);border-color:var(--accent)}
.switch.on .knob{left:18px;background:var(--on-accent)}
.switch:disabled{opacity:.5;cursor:default}

.kv{display:grid;grid-template-columns:auto 1fr auto;gap:8px 12px;align-items:center;justify-items:start}
.kv .mono{justify-self:stretch}
.k{font-size:12px;color:var(--text-dim)}
.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.btnrow{display:flex;gap:6px}
.gbtn{border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);font-size:12px;font-weight:600;padding:5px 11px;cursor:pointer}
.gbtn:hover:not(:disabled){color:var(--text);border-color:var(--border-strong)}
.gbtn:disabled{opacity:.5;cursor:default}
.note{font-size:12px;color:var(--warn-fg)}
.cmd{display:flex;align-items:center;gap:10px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px}
.cmd .mono{flex:1;white-space:normal;word-break:break-all;color:var(--text-dim)}

.perm{padding:16px 18px 10px}
.pgrp{font-size:10.5px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint);margin:12px 0 4px}
.prow{display:flex;align-items:center;gap:12px;padding:7px 0}
.prow + .prow{border-top:1px dashed var(--border)}
.pn{font-size:13px;display:flex;align-items:center;gap:8px}
.pd{font-size:11.5px;color:var(--text-faint)}

.empty{padding:16px 18px;font-size:12.5px;color:var(--text-faint)}
.log{max-height:280px;overflow-y:auto;padding:6px 0}
.lrow{display:flex;align-items:baseline;gap:12px;padding:6px 18px;font-size:12.5px}
.lrow + .lrow{border-top:1px solid var(--border)}
.lrow .t{flex-shrink:0;font-variant-numeric:tabular-nums;color:var(--text-faint);font-size:11.5px}
.lrow .a{flex:1;color:var(--text-dim)}
.lrow .a b{color:var(--text);font-weight:600}
.lrow .a .n{font-weight:700;color:var(--text-faint)}
.lrow .c{flex-shrink:0;font-size:11px;font-weight:700;color:var(--danger)}
</style>
