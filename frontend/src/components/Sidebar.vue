<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useAccount } from '../composables/useAccount'
import { useAgentStatus } from '../composables/useAgentStatus'
import { SETTINGS_SECTIONS, type SettingsSection } from '../settingsSections'
import type { ViewId } from '../App.vue'

const props = defineProps<{ current: ViewId; section: SettingsSection }>()
const emit = defineEmits<{ navigate: [ViewId]; section: [SettingsSection] }>()

const { subscription, refresh } = useAccount()
onMounted(refresh)

// Settings opens into its sections, nested under it; folded, it carries Agent activity's badge so a refused or
// failed agent call still shows from any view.
const { status, statusText, freshCount, freshProblem } = useAgentStatus()
const inSettings = computed(() => props.current === 'settings')
// on: an agent connected · idle: waiting for one · bad: on, but the listener isn't running
const agentDot = computed(() => {
  const s = status.value
  if (!s?.config.enabled) return null
  return !s.running ? 'bad' : s.connected ? 'on' : 'idle'
})

const version = __APP_VERSION__ // from frontend/package.json via Vite define
</script>

<template>
  <aside class="sidebar">
    <div class="navgroup">Workspace</div>
    <div class="navitem" :class="{ active: current === 'generate' }" @click="emit('navigate', 'generate')">
      <i>✦</i> Generate
    </div>
    <div class="navitem" :class="{ active: current === 'works' }" @click="emit('navigate', 'works')">
      <i>❐</i> Works
    </div>
    <div class="navitem" :class="{ active: current === 'library' }" @click="emit('navigate', 'library')">
      <i>❏</i> Library
    </div>
    <div class="navitem" :class="{ active: current === 'presets' }" @click="emit('navigate', 'presets')">
      <i>◈</i> Presets
    </div>

    <div class="navgroup">Account</div>
    <div class="navitem" :class="{ parent: inSettings }" @click="emit('navigate', 'settings')">
      <i>⚙</i> Settings<span class="sp"></span>
      <span v-if="!inSettings && freshCount" class="badge" :class="{ bad: freshProblem }"
        :title="`${freshCount} new in Agent activity`">{{ freshCount }}</span>
      <span class="caret"></span>
    </div>
    <div v-show="inSettings" class="subnav">
      <div v-for="s in SETTINGS_SECTIONS" :key="s.id" class="subitem" :class="{ active: section === s.id }"
        @click="emit('section', s.id)">
        {{ s.title }}<span class="sp"></span>
        <span v-if="s.id === 'agents' && agentDot" class="dot" :class="agentDot" :title="statusText"></span>
        <span v-if="s.id === 'activity' && section !== 'activity' && freshCount" class="badge" :class="{ bad: freshProblem }"
          :title="`${freshCount} new`">{{ freshCount }}</span>
      </div>
    </div>

    <!-- one account card: tier + balance, the app version as its footer line -->
    <div class="acct">
      <div v-if="subscription" class="top" :title="`${subscription.tier_name} subscription — remaining Anlas`">
        <span class="tier">{{ subscription.tier_name }}</span>
        <span class="bal"><span class="dia">◆</span><span class="amt">{{ subscription.anlas.toLocaleString() }}</span><span class="lbl">Anlas</span></span>
      </div>
      <div class="foot"><span>novelai studio</span><span class="ver">v{{ version }}</span></div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar{width:224px;flex-shrink:0;background:var(--surface-1);border-right:1px solid var(--border);
  display:flex;flex-direction:column;padding:10px 12px 12px}
.navgroup{font-size:11px;font-weight:700;color:var(--text-faint);letter-spacing:.4px;
  text-transform:uppercase;padding:10px 10px 6px}
.navitem{display:flex;align-items:center;gap:11px;padding:8px 10px;border-radius:var(--radius);
  color:var(--text-dim);font-weight:500;cursor:pointer;margin-bottom:2px}
.navitem i{width:18px;text-align:center;font-style:normal;font-size:16px;opacity:.9}
.navitem:not(.disabled):hover{background:var(--surface-3);color:var(--text)}
.navitem.active{background:var(--nav-active);color:var(--accent);font-weight:600}
.navitem.disabled{opacity:.55;cursor:default}
.navitem .soon{margin-left:auto;font-size:10px;font-weight:600;color:var(--text-faint);
  border:1px solid var(--border-strong);padding:0 6px;border-radius:20px}
.sp{margin-left:auto}
/* Settings, open: its sections hang under it on a guide line from the gear; the chevron turns down */
.navitem.parent{color:var(--text);font-weight:600}
.navitem.parent i{color:var(--accent)}
.caret{width:7px;height:7px;margin:0 3px 0 4px;border-right:1.5px solid var(--text-faint);border-bottom:1.5px solid var(--text-faint);
  transform:rotate(-45deg);transition:transform .15s}
.navitem.parent .caret{transform:rotate(45deg) translate(-2px,-2px)}
.subnav{display:flex;flex-direction:column;gap:1px;margin:0 0 6px 19px;padding-left:10px;border-left:1px solid var(--border-strong)}
.subitem{position:relative;display:flex;align-items:center;gap:8px;padding:6px 10px;border-radius:var(--radius);
  color:var(--text-dim);font-size:13px;font-weight:500;cursor:pointer}
.subitem:hover{background:var(--surface-3);color:var(--text)}
.subitem.active{background:var(--nav-active);color:var(--accent);font-weight:600}
.subitem.active::before{content:"";position:absolute;left:-11px;top:6px;bottom:6px;width:2px;border-radius:2px;background:var(--accent)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--text-faint)}
.dot.on{background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 25%,transparent)}
.dot.bad{background:var(--danger)}
.badge{font-size:10.5px;font-weight:700;line-height:17px;min-width:20px;text-align:center;border-radius:9px;padding:0 6px;
  background:var(--accent);color:var(--on-accent)}
.badge.bad{background:var(--danger)}
.acct{margin-top:auto;border:1px solid var(--border);border-radius:var(--radius-lg);background:var(--surface-2);overflow:hidden}
.acct .top{display:flex;align-items:center;gap:8px;padding:8px 10px;font-size:12px}
.acct .tier{font-size:10px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;color:var(--accent);
  background:var(--nav-active);border:1px solid color-mix(in srgb,var(--accent) 35%,var(--border));border-radius:10px;padding:1px 7px}
.acct .bal{margin-left:auto;display:flex;align-items:center;gap:4px}
.acct .dia{color:var(--accent);font-size:11px}
.acct .amt{font-weight:700;color:var(--text);font-variant-numeric:tabular-nums}
.acct .lbl{color:var(--text-faint);font-size:11px}
.acct .foot{display:flex;align-items:center;justify-content:space-between;padding:5px 10px;font-size:10.5px;color:var(--text-faint);
  background:color-mix(in srgb,var(--surface-1) 50%,transparent)}
.acct .top + .foot{border-top:1px solid var(--border)}
.acct .ver{font-variant-numeric:tabular-nums}
</style>
