<script setup lang="ts">
/* Settings → General: appearance, and how work is saved — the auto-save interval and the downloads folder. */
import { onMounted, ref } from 'vue'
import { getAppSettings, patchAppSettings } from '../api'
import { useTheme } from '../composables/useTheme'
import { useToast } from '../composables/useToast'
import { browseFolder, openInFileManager } from '../electron'

const { theme, accent, accents } = useTheme()
const { push } = useToast()

const interval = ref(300)
const INTERVALS = [{ v: 60, l: '1 min' }, { v: 300, l: '5 min' }, { v: 600, l: '10 min' }, { v: 1800, l: '30 min' }]
const downloadDir = ref('')

onMounted(async () => {
  try {
    const s = await getAppSettings()
    interval.value = s.autosave_interval_s
    downloadDir.value = s.download_dir
  } catch { /* keep the defaults */ }
})

async function changeInterval(v: number) {
  interval.value = v
  try { await patchAppSettings({ autosave_interval_s: v }) }
  catch (e) { push(e instanceof Error ? e.message : 'Could not save', 'err') }
}
async function changeDownloads() {
  const dir = await browseFolder('Choose the downloads folder')
  if (!dir) return
  try { downloadDir.value = (await patchAppSettings({ download_dir: dir })).download_dir; push('Downloads folder changed', 'ok') }
  catch (e) { push(e instanceof Error ? e.message : 'Could not change the folder', 'err') }
}
</script>

<template>
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
        <button v-for="a in accents" :key="a.a" class="sw" :class="{ active: accent === a.a }"
          :style="{ background: a.a }" :title="a.name" @click="accent = a.a"></button>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-hd">Saving</div>
    <div class="setting">
      <div class="txt"><div class="name">Auto-save interval</div>
        <div class="desc">Works also save when you leave the Generate tab and when the app closes.</div></div>
      <div class="seg">
        <button v-for="i in INTERVALS" :key="i.v" :class="{ active: interval === i.v }" @click="changeInterval(i.v)">{{ i.l }}</button>
      </div>
    </div>
    <div class="setting col">
      <div class="txt"><div class="name">Downloads folder</div>
        <div class="desc">Where “Download” saves images — and where an agent's generated images go if saving them to the vault fails.</div></div>
      <div class="row">
        <span class="mono dir" :title="downloadDir">{{ downloadDir }}</span>
        <button class="gbtn" :disabled="!downloadDir" @click="openInFileManager(downloadDir)">Open</button>
        <button class="gbtn" @click="changeDownloads">Change…</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.theme-seg button{width:80px}
.swatches{display:flex;gap:8px;flex-shrink:0}
.sw{width:24px;height:24px;border-radius:50%;cursor:pointer;border:2px solid transparent;padding:0}
.sw.active{border-color:var(--text-dim)}
.dir{flex:1}
</style>
