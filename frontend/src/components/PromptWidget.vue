<script setup lang="ts">
/* The library-zone chrome (design/prompt-widget-mockup.html, rev 5). Block 1 scope: header with a
 * collapse chevron, empty hint, footer with the pinned count + Library escape hatch. The pinned
 * blocks themselves are the zone's child block nodes — CanvasBoard packs them into the column
 * (canvas/pack.ts); this component never owns palette state. */
import type { ZoneNode } from '../types'

defineProps<{ data: ZoneNode['data']; selected: boolean; count: number }>()
defineEmits<{ toggle: []; 'open-library': [] }>()
</script>

<template>
  <div class="pwidget" :class="{ selected, collapsed: data.collapsed }">
    <div class="pwhd">
      <span class="picon">✦</span>
      <span class="ptitle">Prompt blocks</span>
      <span class="anchor-tag">anchor</span>
      <button class="pcollapse nodrag" :title="data.collapsed ? 'Expand' : 'Collapse to header'"
        @pointerdown.stop @click.stop="$emit('toggle')">{{ data.collapsed ? '▸' : '▾' }}</button>
    </div>
    <template v-if="!data.collapsed">
      <div class="pwbody">
        <div v-if="!count" class="pwhint">Drag prompt blocks here for quick access.</div>
      </div>
      <div class="pwfoot">
        <span>{{ count }} pinned</span>
        <a class="plib nodrag" @pointerdown.stop @click.stop="$emit('open-library')">Open Library ↗</a>
      </div>
    </template>
  </div>
</template>

<style scoped>
.pwidget{width:100%;height:100%;display:flex;flex-direction:column;border:1.5px solid var(--border-strong);
  border-radius:12px;overflow:hidden;background:color-mix(in srgb,var(--surface-1) 60%,transparent)}
.pwidget.selected{border-color:var(--accent)}
.pwhd{display:flex;align-items:center;gap:8px;height:38px;flex-shrink:0;padding:0 12px;
  border-bottom:1px solid var(--border);background:var(--surface-1);font-weight:600;font-size:13px}
.pwidget.collapsed .pwhd{border-bottom:0}
.picon{font-style:normal}
.ptitle{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.anchor-tag{margin-left:auto;font-size:10px;font-weight:600;color:var(--accent);
  background:color-mix(in srgb,var(--accent) 16%,transparent);padding:1px 7px;border-radius:20px}
.pcollapse{border:0;background:transparent;color:var(--text-faint);cursor:pointer;font-size:11px;padding:2px 4px}
.pcollapse:hover{color:var(--text)}
.pwbody{flex:1;min-height:0}
.pwhint{padding:16px;font-size:12px;color:var(--text-faint);text-align:center}
.pwfoot{flex-shrink:0;display:flex;align-items:center;gap:8px;border-top:1px solid var(--border);
  background:var(--surface-1);padding:6px 12px;font-size:11px;color:var(--text-faint)}
.plib{margin-left:auto;color:var(--accent);cursor:pointer;font-weight:600;text-decoration:none}
</style>
