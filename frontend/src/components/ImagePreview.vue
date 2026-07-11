<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'
import { useImagePreview } from '../composables/useImagePreview'

const { src, close } = useImagePreview()

// Esc closes; only listen while open.
function onKey(e: KeyboardEvent) { if (e.key === 'Escape') close() }
watch(src, (v) => {
  if (v) window.addEventListener('keydown', onKey)
  else window.removeEventListener('keydown', onKey)
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <div v-if="src" class="preview-back" @click="close" @contextmenu.prevent>
      <img class="preview-img" :src="src" alt="preview" @click.stop draggable="false" />
      <button class="preview-close" title="Close (Esc)" @click.stop="close">✕</button>
    </div>
  </Teleport>
</template>

<style scoped>
.preview-back{position:fixed;inset:0;z-index:2500;background:rgba(0,0,0,.82);display:flex;align-items:center;justify-content:center;padding:32px;cursor:zoom-out}
.preview-img{max-width:95vw;max-height:95vh;object-fit:contain;border-radius:6px;box-shadow:0 18px 60px rgba(0,0,0,.6);cursor:default}
.preview-close{position:fixed;top:18px;right:20px;width:36px;height:36px;border-radius:50%;border:1px solid var(--border-strong);
  background:var(--surface-1);color:var(--text-dim);font-size:15px;cursor:pointer;display:flex;align-items:center;justify-content:center}
.preview-close:hover{color:var(--text);border-color:var(--text-faint)}
</style>
