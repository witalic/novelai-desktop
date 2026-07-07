<script setup lang="ts">
import type { ViewId } from '../App.vue'

defineProps<{ current: ViewId }>()
const emit = defineEmits<{ navigate: [ViewId] }>()
</script>

<template>
  <aside class="sidebar">
    <div class="brand"><span class="logo">N</span><div>novelai studio<div class="sub">preview build</div></div></div>

    <div class="navgroup">Workspace</div>
    <div class="navitem" :class="{ active: current === 'generate' }" @click="emit('navigate', 'generate')">
      <i>✦</i> Generate
    </div>
    <div class="navitem disabled"><i>▤</i> Library <span class="soon">soon</span></div>
    <div class="navitem disabled"><i>❏</i> Presets <span class="soon">soon</span></div>

    <div class="navgroup">Account</div>
    <div class="navitem" :class="{ active: current === 'settings' }" @click="emit('navigate', 'settings')">
      <i>⚙</i> Settings
    </div>

    <div class="bottom"><div class="ver">v0.1 · preview</div></div>
  </aside>
</template>

<style scoped>
.sidebar{width:224px;flex-shrink:0;background:var(--surface-1);border-right:1px solid var(--border);
  display:flex;flex-direction:column;padding:14px 12px}
.brand{display:flex;align-items:center;gap:10px;padding:6px 8px 16px;font-weight:600}
.logo{width:28px;height:28px;border-radius:7px;background:var(--accent);color:#fff;
  display:flex;align-items:center;justify-content:center;font-weight:700;font-size:15px}
.brand .sub{font-size:11px;color:var(--text-faint);font-weight:500}
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
.bottom{margin-top:auto;border-top:1px solid var(--border);padding-top:12px}
.ver{font-size:11px;color:var(--text-faint);padding:4px 8px}
</style>
