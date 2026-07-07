<script setup lang="ts">
import { ref } from 'vue'
import Sidebar from './components/Sidebar.vue'
import GenerateView from './views/GenerateView.vue'
import SettingsView from './views/SettingsView.vue'

export type ViewId = 'generate' | 'settings'
const view = ref<ViewId>('generate')
const views = { generate: GenerateView, settings: SettingsView }
</script>

<template>
  <div class="app">
    <Sidebar :current="view" @navigate="view = $event" />
    <component :is="views[view]" />
  </div>
</template>

<style scoped>
.app { display: flex; height: 100vh; overflow: hidden; }
</style>
