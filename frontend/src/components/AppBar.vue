<script setup lang="ts">
/* The app bar (design/ui-polish.html §7–8): the window has no OS title bar, so this slim strip carries the
 * brand and our own window buttons, and is the window's drag handle (double-click maximizes — native).
 * In a plain browser (--web) there are no window controls, so only the brand shows. */
import { onMounted, onUnmounted, ref } from 'vue'
import { windowControls } from '../electron'

const controls = windowControls()
const maximized = ref(false)
let dispose: (() => void) | null = null
onMounted(async () => {
  if (!controls) return
  maximized.value = await controls.isMaximized()
  dispose = controls.onMaximizedChange((m) => { maximized.value = m })
})
onUnmounted(() => dispose?.())
</script>

<template>
  <header class="appbar">
    <div class="brand"><span class="logo">N</span>novelai studio</div>
    <div class="drag"></div>
    <template v-if="controls">
      <button class="wc" title="Minimize" @click="controls.minimize()">
        <svg viewBox="0 0 10 10"><path d="M0 5.5h10" /></svg>
      </button>
      <button class="wc" :title="maximized ? 'Restore' : 'Maximize'" @click="controls.toggleMaximize()">
        <svg v-if="maximized" viewBox="0 0 10 10"><path d="M2.5 2.5V.5h7v7h-2" /><rect x=".5" y="2.5" width="7" height="7" /></svg>
        <svg v-else viewBox="0 0 10 10"><rect x=".5" y=".5" width="9" height="9" /></svg>
      </button>
      <button class="wc close" title="Close" @click="controls.close()">
        <svg viewBox="0 0 10 10"><path d="M.5.5l9 9M9.5.5l-9 9" /></svg>
      </button>
    </template>
  </header>
</template>

<style scoped>
.appbar{height:var(--appbar-h);flex-shrink:0;display:flex;align-items:center;background:var(--surface-1);border-bottom:1px solid var(--border);
  user-select:none;-webkit-app-region:drag}
.brand{display:flex;align-items:center;gap:9px;padding:0 14px;font-size:12.5px;font-weight:650;color:var(--text)}
.logo{width:20px;height:20px;border-radius:5px;background:var(--accent);color:var(--on-accent);display:flex;align-items:center;
  justify-content:center;font-weight:800;font-size:11.5px}
.drag{flex:1;height:100%}
.wc{width:46px;height:100%;border:0;background:transparent;color:var(--text-dim);display:flex;align-items:center;justify-content:center;
  cursor:default;-webkit-app-region:no-drag}
.wc svg{width:10px;height:10px;stroke:currentColor;fill:none;stroke-width:1.1}
.wc:hover{background:var(--surface-3);color:var(--text)}
.wc.close:hover{background:#c42b1c;color:#fff} /* Windows' own close-button red — the convention, not a theme colour */
</style>
