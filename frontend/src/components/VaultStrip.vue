<script setup lang="ts">
/* The strip under the app bar while the active vault can't be reached (design/vault-unavailable.html §1): which
 * folder, that nothing is saved meanwhile and what's open stays, Try again, and the way to Settings → Vaults. */
import { useVault } from '../composables/useVault'

defineProps<{ inSettings?: boolean }>() // Settings → Vaults is on screen: no button to it
const emit = defineEmits<{ settings: [] }>()
const { unreachable, checking, check } = useVault()
</script>

<template>
  <div v-if="unreachable" class="vstrip" role="alert">
    <span class="ic">⚠</span>
    <div class="msg"><b>Vault unavailable</b> — <span class="path">{{ unreachable }}</span> can't be reached.
      <span class="sub">Nothing is saved until it's back; what you have open stays here.</span></div>
    <span class="retry">{{ checking ? 'Checking…' : 'Trying again every 20 s' }}</span>
    <button class="sbtn pri" :disabled="checking" @click="check">Try again</button>
    <button v-if="!inSettings" class="sbtn" @click="emit('settings')">Vault settings</button>
  </div>
</template>

<style scoped>
.vstrip{flex-shrink:0;display:flex;align-items:center;gap:12px;padding:8px 14px;font-size:12.5px;
  background:color-mix(in srgb,var(--warn) 14%,var(--surface-1));border-bottom:1px solid color-mix(in srgb,var(--warn) 45%,var(--border))}
.ic{color:var(--warn-fg);font-weight:800}
.msg{flex:1;min-width:0;color:var(--text)}
.path{font-family:ui-monospace,Consolas,monospace;font-size:11.5px;word-break:break-all}
.sub{color:var(--text-dim)}
.retry{font-size:11.5px;color:var(--text-faint);white-space:nowrap}
.sbtn{border:1px solid var(--border-strong);background:var(--surface-1);color:var(--text);border-radius:var(--radius);padding:5px 11px;
  font:inherit;font-size:12px;font-weight:600;white-space:nowrap;cursor:pointer}
.sbtn:hover:not(:disabled){border-color:var(--text-faint)}
.sbtn.pri{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.sbtn.pri:hover:not(:disabled){background:var(--accent-strong)}
.sbtn:disabled{opacity:.6;cursor:default}
</style>
