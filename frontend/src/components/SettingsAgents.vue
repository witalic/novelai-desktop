<script setup lang="ts">
/* Settings → Agents (design/settings-layout.html, controls from design/settings-agent-access.html): the switch for
 * the app's MCP endpoint and how to connect an agent (address, token, a ready `claude mcp add` command), what
 * agents may do, the Anlas limits for their generations, and the last few things they did. The port is the app's
 * own business (owner decision) — only the address in use is shown. */
import { computed } from 'vue'
import { revealAgentToken, type AgentPatch, type AgentPermissions } from '../api'
import { copyText } from '../clipboard'
import { useAccount } from '../composables/useAccount'
import { useAgentStatus } from '../composables/useAgentStatus'
import { useConfirm } from '../composables/useConfirm'
import { useToast } from '../composables/useToast'
import ActivityRow from './ActivityRow.vue'

const emit = defineEmits<{ 'open-activity': [] }>()
const { push } = useToast()
const { confirm } = useConfirm()
const agent = useAgentStatus()
const { status: st, busy, statusText } = agent

// Grouped as in the mockup; deleting is marked and off by default (backend AgentPermissions).
const PERMS: { group: string; items: { key: keyof AgentPermissions; name: string; desc: string; danger?: true }[] }[] = [
  { group: 'Read', items: [
    { key: 'read', name: 'Browse the Library and works',
      desc: 'blocks, categories, tags, works, image metadata and images, models, presets, your Anlas balance' },
  ] },
  { group: 'Library', items: [
    { key: 'blocks_write', name: 'Create and edit blocks',
      desc: 'same rules as Library import — duplicates are skipped, new categories are made' },
    { key: 'blocks_delete', name: 'Delete blocks', desc: 'removes blocks from the Library', danger: true },
  ] },
  { group: 'Works', items: [
    { key: 'works_write', name: 'Create works, edit titles and image metadata', desc: 'tags, descriptions, favorites' },
    { key: 'combos_write', name: 'Create and edit combinations', desc: 'ready block sets inside a work' },
    { key: 'works_delete', name: 'Delete images and combinations', desc: 'permanent', danger: true },
  ] },
  { group: 'Generation', items: [
    { key: 'generate', name: 'Generate images', desc: "results go into the work's gallery — Quick access" },
  ] },
]

const cfg = computed(() => st.value?.config)
const spending = computed(() => cfg.value?.spending)
const spentPct = computed(() => Math.min(100, ((st.value?.spent_today ?? 0) / (spending.value?.daily_budget || 1)) * 100))
const { subscription } = useAccount()
const opus = computed(() => !!subscription.value?.active && (subscription.value?.tier ?? 0) >= 3) // free generations
const recent = computed(() => st.value?.activity.slice(0, 3) ?? [])

const maskedToken = computed(() => (st.value?.token_hint ? `••••••••••••••••••••${st.value.token_hint}` : 'issued when you switch access on'))
// --scope user: available in every project, kept in the user's own config — never a committable .mcp.json.
const command = (token: string) =>
  `claude mcp add --scope user --transport http novelai ${st.value?.address} --header "Authorization: Bearer ${token}"`

async function save(patch: AgentPatch) {
  const before = st.value?.error
  try {
    if (await agent.save(patch) && st.value?.error && st.value.error !== before) push(st.value.error, 'err')
  } catch (e) {
    push(e instanceof Error ? e.message : 'Could not save', 'err') // nothing changed — the page still shows the truth
  }
}
const setPerm = (key: keyof AgentPermissions, on: boolean) => save({ permissions: { [key]: on } })
// A limit is typed, then saved on Enter / leaving the field; whatever isn't saved (not a whole number ≥ 1, refused,
// or a save already running) snaps back to the saved value.
async function setLimit(key: 'max_per_request' | 'daily_budget', ev: Event) {
  const input = ev.target as HTMLInputElement
  const value = Math.floor(Number(input.value))
  if (Number.isFinite(value) && value >= 1 && value !== spending.value?.[key]) await save({ spending: { [key]: value } })
  input.value = String(spending.value?.[key] ?? '')
}

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
  try { if (await agent.regenerate()) push('New agent token issued', 'ok') }
  catch (e) { push(e instanceof Error ? e.message : 'Could not regenerate', 'err') }
}
</script>

<template>
  <p class="psub">AI agents on this computer (Claude Code, …) working with your vault through MCP while the app is open.</p>

  <div class="card">
    <div class="card-hd">Access<span class="sp"></span>
      <span class="status" :class="{ on: cfg?.enabled && st?.running, bad: cfg?.enabled && !st?.running }"><i></i>{{ statusText }}</span>
    </div>
    <div class="setting">
      <div class="txt"><div class="name">Allow agents to connect</div>
        <div class="desc">Loopback only — never reachable from other machines.</div></div>
      <button class="switch" :class="{ on: cfg?.enabled }" role="switch" :aria-checked="!!cfg?.enabled" :disabled="!cfg || busy"
        title="Allow agents to connect" @click="save({ enabled: !cfg?.enabled })"><span class="knob"></span></button>
    </div>
    <div v-show="cfg?.enabled" class="setting col">
      <div class="txt"><div class="name">Connection</div>
        <div class="desc">A fixed address so agents find the app across restarts. The agent token is its own key, kept in the OS
          keychain — not your NovelAI token.</div></div>
      <div v-if="st?.error" class="err">{{ st.error }}</div>
      <div v-if="st?.note" class="note">{{ st.note }}</div>
      <div class="kv">
        <span class="k">Address</span><span class="mono">{{ st?.address }}</span>
        <button class="gbtn" @click="copy('address')">Copy</button>
        <span class="k">Agent token</span><span class="mono">{{ maskedToken }}</span>
        <div class="row"><button class="gbtn" :disabled="!st?.token_hint" @click="copy('token')">Copy</button>
          <button class="gbtn" :disabled="!st?.token_hint || busy" @click="regenerate">Regenerate</button></div>
      </div>
      <div class="cmd">
        <div class="mono">{{ command(st?.token_hint ? `••••${st.token_hint}` : '…') }}</div>
        <button class="gbtn" :disabled="!st?.token_hint" @click="copy('command')">Copy</button>
      </div>
      <div class="hint">Paste into a terminal to connect <b>Claude Code</b> (the copy carries the real token). Regenerating the
        token disconnects every agent until it is updated.</div>
    </div>
  </div>

  <div class="card">
    <div class="card-hd">Permissions<span class="sp"></span><span class="hint">each switch applies to the agent's next call</span></div>
    <div class="perm">
      <template v-for="g in PERMS" :key="g.group">
        <div class="pgrp">{{ g.group }}</div>
        <div v-for="p in g.items" :key="p.key" class="prow" :class="{ dng: p.danger }">
          <div><div class="pn">{{ p.name }}<span v-if="p.danger" class="tag">DELETE</span></div><div class="pd">{{ p.desc }}</div></div>
          <span class="sp"></span>
          <button class="switch" :class="{ on: cfg?.permissions[p.key], dng: p.danger }" role="switch" :aria-checked="!!cfg?.permissions[p.key]"
            :disabled="!cfg || busy" :title="p.name" @click="setPerm(p.key, !cfg?.permissions[p.key])"><span class="knob"></span></button>
        </div>
      </template>
    </div>
  </div>

  <div class="card">
    <div class="card-hd">Anlas limits</div>
    <div class="setting col">
      <div class="txt"><div class="desc">Free generations always run while “Generate images” is on. A paid one runs only when you ask
        the agent for it and only within these limits — over a limit it is refused, never queued.</div></div>
      <div v-if="opus" class="freebox"><span class="ok">✓</span><div><b>Free with Opus:</b> up to 1024×1024 px (e.g. 832×1216),
        ≤ 28 steps, 1 image.</div></div>
      <div v-else class="freebox none">This account has no free generations — every agent generation is paid.</div>
      <div class="lset">
        <div class="txt"><div class="name">Allow paid generations</div><div class="desc">bigger sizes, more steps, several images at once</div></div>
        <button class="switch" :class="{ on: spending?.paid }" role="switch" :aria-checked="!!spending?.paid" :disabled="!cfg || busy"
          title="Allow paid generations" @click="save({ spending: { paid: !spending?.paid } })"><span class="knob"></span></button>
      </div>
      <div class="lset" :class="{ off: !spending?.paid }">
        <div class="txt"><div class="name">Max per generation</div><div class="desc">one request may not cost more than this</div></div>
        <label class="anlas">◆<input class="numin" type="text" inputmode="numeric" :value="spending?.max_per_request"
          :disabled="!spending?.paid || busy" aria-label="Max Anlas per generation"
          @change="setLimit('max_per_request', $event)" @keydown.enter="($event.target as HTMLInputElement).blur()" /></label>
      </div>
      <div class="lset" :class="{ off: !spending?.paid }">
        <div class="txt"><div class="name">Daily budget</div><div class="desc">resets at local midnight</div></div>
        <label class="anlas">◆<input class="numin" type="text" inputmode="numeric" :value="spending?.daily_budget"
          :disabled="!spending?.paid || busy" aria-label="Daily Anlas budget"
          @change="setLimit('daily_budget', $event)" @keydown.enter="($event.target as HTMLInputElement).blur()" /></label>
      </div>
      <div class="meter"><span class="hint">Spent today</span><div class="bar"><div class="barfill" :style="{ width: `${spentPct}%` }"></div></div>
        <span class="hint"><b>◆ {{ st?.spent_today ?? 0 }}</b> of {{ spending?.daily_budget }}</span></div>
    </div>
  </div>

  <div class="card">
    <div class="card-hd">Recent activity</div>
    <ActivityRow v-for="a in recent" :key="a.seq" :entry="a" />
    <div v-if="!recent.length" class="empty">No agent activity yet. Everything an agent reads or changes — and every refused
      call — shows up in Agent activity.</div>
    <div class="card-ft"><span v-if="recent.length" class="hint">last {{ recent.length }} of {{ st?.activity.length }}</span>
      <span class="sp"></span><button class="link" @click="emit('open-activity')">All activity →</button></div>
  </div>
</template>

<style scoped>
.psub{font-size:12.5px;color:var(--text-dim);margin:0}
.status{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:600;color:var(--text-faint)}
.status i{width:7px;height:7px;border-radius:50%;background:var(--text-faint)}
.status.on{color:var(--ok)}.status.on i{background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 25%,transparent)}
.status.bad{color:var(--danger)}.status.bad i{background:var(--danger)}

.kv{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:8px 12px;align-items:center;justify-items:start}
.kv .mono{justify-self:stretch}
.k{font-size:12px;color:var(--text-dim)}
.cmd{display:flex;align-items:center;gap:10px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px}
.cmd .mono{flex:1;white-space:normal;word-break:break-all;color:var(--text-dim);background:none;border:0;padding:0}

.perm{padding:4px 18px 10px}
.pgrp{font-size:10.5px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--text-faint);margin:12px 0 4px}
.prow{display:flex;align-items:center;gap:12px;padding:7px 0}
.prow + .prow{border-top:1px dashed var(--border)}
.pn{font-size:13px;display:flex;align-items:center;gap:8px}
.pd{font-size:11.5px;color:var(--text-faint)}
.prow.dng .pn{color:var(--danger)}
.tag{font-size:9.5px;font-weight:800;letter-spacing:.3px;border-radius:9px;padding:0 6px;line-height:15px;border:1px solid
  color-mix(in srgb,var(--danger) 50%,var(--border));color:var(--danger);background:color-mix(in srgb,var(--danger) 10%,transparent)}

.freebox{display:flex;gap:10px;align-items:flex-start;font-size:12px;color:var(--text-dim);padding:9px 12px;border-radius:var(--radius);
  background:color-mix(in srgb,var(--ok) 9%,transparent);border:1px solid color-mix(in srgb,var(--ok) 35%,var(--border))}
.freebox b,.freebox .ok{color:var(--ok)}
.freebox.none{background:var(--surface-2);border-color:var(--border)}
.lset{display:flex;align-items:center;justify-content:space-between;gap:20px}
.lset.off{opacity:.55}
.anlas{display:flex;align-items:center;gap:6px;color:var(--text-dim)}
.numin{width:84px;font:inherit;font-size:13px;color:var(--text);background:var(--surface-2);border:1px solid var(--border);
  border-radius:var(--radius);padding:6px 10px;outline:none;text-align:right}
.numin:focus{border-color:var(--accent)}
.meter{display:flex;align-items:center;gap:10px}
.bar{flex:1;height:6px;background:var(--surface-3);border-radius:20px;overflow:hidden}
.barfill{height:100%;background:var(--accent)}
</style>
