<script setup lang="ts">
import { ref } from 'vue'
import Sidebar from './components/Sidebar.vue'
import GenerateView from './views/GenerateView.vue'
import SettingsView from './views/SettingsView.vue'
import { useTheme } from './composables/useTheme'
import { useToast } from './composables/useToast'

useTheme()
export type ViewId = 'generate' | 'settings'
const view = ref<ViewId>('generate')
const views = { generate: GenerateView, settings: SettingsView }
const { toasts } = useToast()
</script>

<template>
  <div class="app">
    <Sidebar :current="view" @navigate="view = $event" />
    <KeepAlive>
      <component :is="views[view]" />
    </KeepAlive>

    <div class="toasts">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="t.kind">
        <i>{{ t.kind === 'ok' ? '✓' : '⚠' }}</i>{{ t.text }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.app { display: flex; height: 100vh; overflow: hidden; }
.toasts { position: fixed; right: 18px; bottom: 18px; display: flex; flex-direction: column; gap: 8px; z-index: 1000; }
.toast { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 500; padding: 10px 14px;
  border-radius: var(--radius); background: var(--surface-2); border: 1px solid var(--border);
  color: var(--text); box-shadow: 0 6px 24px rgba(0, 0, 0, .4); }
.toast i { font-style: normal; font-weight: 700; }
.toast.ok i { color: #1f845a; }
.toast.err { border-color: color-mix(in srgb, #e2483d 45%, var(--border)); }
.toast.err i { color: #e2483d; }
</style>
