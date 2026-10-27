<script setup lang="ts">
// The toolbar's "Tags" button (design/works-tags.html, A): opens and closes the tag panel (TagPanel) under the toolbar,
// and says how many tags are picked.
defineProps<{ open: boolean; picked: number }>()
const emit = defineEmits<{ toggle: [] }>()
</script>

<template>
  <button class="tagbtn" :class="{ on: open || picked > 0 }" :aria-expanded="open" @click="emit('toggle')">
    Tags <span v-if="picked" class="badge">{{ picked }}</span><span class="car">{{ open ? '▴' : '▾' }}</span>
  </button>
</template>

<style scoped>
.tagbtn { height: 32px; display: inline-flex; align-items: center; gap: 7px; border: 1px solid var(--border-strong);
  background: var(--surface-2); color: var(--text-dim); border-radius: var(--radius); padding: 0 12px; font: inherit;
  font-size: 12.5px; font-weight: 600; white-space: nowrap; cursor: pointer; }
.tagbtn:hover { color: var(--text); }
.tagbtn.on { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); background: var(--nav-active); color: var(--accent); }
.badge { min-width: 18px; height: 18px; border-radius: 9px; background: var(--accent); color: var(--on-accent);
  font-size: 10.5px; font-weight: 700; display: inline-flex; align-items: center; justify-content: center; padding: 0 5px; }
.car { font-size: 10px; opacity: .8; }
</style>
