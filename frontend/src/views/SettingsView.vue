<script setup lang="ts">
import { useTheme } from '../composables/useTheme'

const { theme, accent, accents } = useTheme()
</script>

<template>
  <section class="settings">
    <div class="hd"><h2>Settings</h2></div>
    <div class="body">
      <div class="card">
        <div class="card-hd">Appearance</div>

        <div class="setting">
          <div class="txt"><div class="name">Theme</div><div class="desc">Light or dark interface.</div></div>
          <div class="seg theme-seg">
            <button :class="{ active: theme === 'light' }" @click="theme = 'light'">Light</button>
            <button :class="{ active: theme === 'dark' }" @click="theme = 'dark'">Dark</button>
          </div>
        </div>

        <div class="setting">
          <div class="txt"><div class="name">Accent color</div><div class="desc">Used for actions and highlights.</div></div>
          <div class="swatches">
            <button
              v-for="a in accents"
              :key="a.a"
              class="sw"
              :class="{ active: accent === a.a }"
              :style="{ background: a.a }"
              :title="a.name"
              @click="accent = a.a"
            ></button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.settings{flex:1;display:flex;flex-direction:column;min-width:0;background:var(--bg)}
.hd{padding:15px 20px;border-bottom:1px solid var(--border);flex-shrink:0}
.hd h2{margin:0;font-size:15px;font-weight:600}
.body{padding:24px;overflow:auto}
.card{max-width:560px;background:var(--surface-1);border:1px solid var(--border);border-radius:var(--radius-lg)}
.card-hd{padding:14px 18px;border-bottom:1px solid var(--border);font-weight:600}
.setting{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px 18px}
.setting + .setting{border-top:1px solid var(--border)}
.txt .name{font-weight:500}
.txt .desc{font-size:12px;color:var(--text-dim);margin-top:2px}
.theme-seg{width:180px}
.theme-seg button{padding:8px 4px}
.swatches{display:flex;gap:8px}
.sw{width:24px;height:24px;border-radius:50%;cursor:pointer;border:2px solid transparent;padding:0}
.sw.active{border-color:var(--text-dim)}
</style>
