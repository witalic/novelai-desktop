<script setup lang="ts">
import ParamFields from './ParamFields.vue'
import type { PanelParams } from '../types'

defineProps<{ params: PanelParams; open: boolean }>()
defineEmits<{ toggle: [] }>()
</script>

<template>
  <aside class="params" :class="{ closed: !open }">
    <button v-if="!open" class="rail" title="Show settings" @click="$emit('toggle')"><span class="railicon">⚙</span></button>

    <template v-else>
      <div class="hd">
        <span>Settings</span>
        <button class="collapse" title="Hide" @click="$emit('toggle')">›</button>
      </div>
      <div class="body">
        <ParamFields :params="params" />
      </div>
    </template>
  </aside>
</template>

<style scoped>
.params{background:var(--surface-1);border-left:1px solid var(--border);display:flex;flex-direction:column;min-height:0;order:1}
.rail{width:100%;height:100%;border:0;background:transparent;color:var(--text-dim);cursor:pointer;display:flex;justify-content:center;padding-top:16px}
.rail:hover{color:var(--text)}.railicon{font-size:18px}
.hd{display:flex;align-items:center;justify-content:space-between;padding:15px 18px;border-bottom:1px solid var(--border);font-weight:600;font-size:15px;flex-shrink:0}
.collapse{border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);width:26px;height:26px;cursor:pointer}
.collapse:hover{color:var(--text);border-color:var(--border-strong)}
.body{padding:16px 18px;overflow:auto;flex:1}
</style>
