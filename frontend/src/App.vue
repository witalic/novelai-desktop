<script setup lang="ts">
import { computed, ref } from 'vue'
import AppBar from './components/AppBar.vue'
import Sidebar from './components/Sidebar.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import ContextMenu from './components/ContextMenu.vue'
import ImagePreview from './components/ImagePreview.vue'
import GenerateView from './views/GenerateView.vue'
import WorksView from './views/WorksView.vue'
import LibraryView from './views/LibraryView.vue'
import PresetsView from './views/PresetsView.vue'
import SettingsView from './views/SettingsView.vue'
import { onAgentNotice } from './composables/useAgentNotices'
import { useTheme } from './composables/useTheme'
import { useToast } from './composables/useToast'
import type { Preset, PresetParams } from './types'

useTheme()
export type ViewId = 'generate' | 'works' | 'library' | 'presets' | 'settings'
const view = ref<ViewId>('generate')
const views = { generate: GenerateView, works: WorksView, library: LibraryView, presets: PresetsView, settings: SettingsView }
const { toasts, push } = useToast()

// Whatever an agent changes is said out loud (security.md: nothing happens silently). A busy agent's burst
// updates the one toast on screen instead of stacking a column of them.
let agentToast = { id: 0, more: 0 }
onAgentNotice((n) => {
  const shown = toasts.value.find((t) => t.id === agentToast.id)
  if (!shown) { agentToast = { id: push(n.message, 'ok'), more: 0 }; return }
  agentToast.more += 1
  shown.text = `${n.message} · and ${agentToast.more} more change${agentToast.more === 1 ? '' : 's'}`
})

const activeWorkId = ref<string | null>(null)

// Opening a work from the Works grid: hand its id to the Generate view and switch to it.
// The trailing '#' bumps the prop even when the same work is reopened, re-triggering the loader.
function openWork(id: string) {
  activeWorkId.value = `${id}#${Date.now()}`
  view.value = 'generate'
}

// Apply a preset: hand its id + params to the Generate view (nonce-bumped like the other cross-view
// signals) and switch there. Seed is absent from PresetParams, so Object.assign leaves it alone.
const applyPreset = ref<{ id: string; params: PresetParams; nonce: number } | null>(null)
function onApplyPreset(preset: Preset) {
  applyPreset.value = { id: preset.id, params: preset.params, nonce: Date.now() }
  view.value = 'generate'
}

// "Open in Library ↗" from the prompt widget: switch to Library pre-filtered by the widget's
// category + tags (favorites are work-local and don't exist there, so they're dropped).
const libraryFilter = ref<{ category: string; tags: string[]; nonce: number } | null>(null)
function openLibraryWithFilter(f: { category: string; tags: string[] }) {
  libraryFilter.value = { category: f.category, tags: [...f.tags], nonce: Date.now() }
  view.value = 'library'
}

// Props/handlers bound only to the active view (avoids attribute fallthrough onto the wrong root).
const viewBindings = computed(() =>
  view.value === 'generate'
    ? { openWorkId: activeWorkId.value,
        applyPreset: applyPreset.value, onNavigate: (v: ViewId) => (view.value = v),
        onOpenLibrary: openLibraryWithFilter }
    : view.value === 'works'
      ? { onOpen: openWork }
      : view.value === 'library'
        ? { filter: libraryFilter.value }
        : view.value === 'presets'
          ? { onApply: onApplyPreset, onNavigate: (v: ViewId) => (view.value = v) }
          : {},
)
</script>

<template>
  <div class="app">
    <AppBar />
    <div class="shell">
      <Sidebar :current="view" @navigate="view = $event" />
      <KeepAlive>
        <component :is="views[view]" v-bind="viewBindings" />
      </KeepAlive>
    </div>

    <div class="toasts">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="t.kind">
        <i>{{ t.kind === 'ok' ? '✓' : '⚠' }}</i>{{ t.text }}
      </div>
    </div>

    <ConfirmDialog />
    <ContextMenu />
    <ImagePreview />
  </div>
</template>

<style scoped>
.app { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
.shell { flex: 1; display: flex; min-height: 0; }
.toasts { position: fixed; right: 18px; bottom: 18px; display: flex; flex-direction: column; gap: 8px; z-index: 3000; }
.toast { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 500; padding: 10px 14px;
  border-radius: var(--radius); background: var(--surface-2); border: 1px solid var(--border);
  color: var(--text); box-shadow: 0 6px 24px rgba(0, 0, 0, .4); }
.toast i { font-style: normal; font-weight: 700; }
.toast.ok i { color: #1f845a; }
.toast.err { border-color: color-mix(in srgb, #e2483d 45%, var(--border)); }
.toast.err i { color: #e2483d; }
</style>
