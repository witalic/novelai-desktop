<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useContextMenu } from '../composables/useContextMenu'

const { state, close, run } = useContextMenu()

// Keep the menu on-screen: nudge left/up when it would overflow the viewport. Labels vary in length, so
// the rendered menu is measured (post-flush, before paint); the item-count estimate only covers that gap.
const menu = ref<HTMLElement | null>(null)
const size = ref({ w: 190, h: 0 })
watch(() => state.value.open && state.value.items, () => {
  if (menu.value) size.value = { w: menu.value.offsetWidth, h: menu.value.offsetHeight }
}, { flush: 'post' })
const pos = computed(() => {
  const w = size.value.w, h = size.value.h || 12 + state.value.items.length * 34
  const x = Math.min(state.value.x, window.innerWidth - w - 8)
  const y = Math.min(state.value.y, window.innerHeight - h - 8)
  return { left: `${Math.max(8, x)}px`, top: `${Math.max(8, y)}px` }
})
</script>

<template>
  <Teleport to="body">
    <template v-if="state.open">
      <div class="cm-back" @click="close" @contextmenu.prevent="close" @wheel="close" />
      <div ref="menu" class="cm" :style="pos" @contextmenu.prevent>
        <button v-for="(it, i) in state.items" :key="i" class="cm-item" :class="{ danger: it.danger }" @click="run(it)">
          <span v-if="it.icon" class="cm-ic">{{ it.icon }}</span>{{ it.label }}
        </button>
      </div>
    </template>
  </Teleport>
</template>

<style scoped>
.cm-back{position:fixed;inset:0;z-index:2600}
.cm{position:fixed;z-index:2601;min-width:172px;background:var(--surface-1);border:1px solid var(--border-strong);
  border-radius:var(--radius);box-shadow:0 12px 32px rgba(0,0,0,.45);padding:5px;display:flex;flex-direction:column}
.cm-item{display:flex;align-items:center;gap:9px;border:0;background:transparent;color:var(--text-dim);font:inherit;
  font-size:12.5px;font-weight:500;text-align:left;padding:7px 10px;border-radius:6px;cursor:pointer;white-space:nowrap}
.cm-item:hover{background:var(--surface-3);color:var(--text)}
.cm-item.danger:hover{background:color-mix(in srgb,#e2483d 16%,var(--surface-3));color:#e2483d}
.cm-ic{width:16px;text-align:center;font-size:13px}
</style>
