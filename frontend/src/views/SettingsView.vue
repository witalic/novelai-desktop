<script setup lang="ts">
/* Settings (design/settings-layout.html): one section at a time — the sidebar lists them under Settings while it
 * is open. Every section stays mounted (v-show), so switching keeps a half-typed token or a scrolled log. */
import { computed, onActivated, onDeactivated, ref } from 'vue'
import { useAgentStatus } from '../composables/useAgentStatus'
import { SETTINGS_SECTIONS, type SettingsSection } from '../settingsSections'
import SettingsActivity from '../components/SettingsActivity.vue'
import SettingsAgents from '../components/SettingsAgents.vue'
import SettingsGeneral from '../components/SettingsGeneral.vue'
import SettingsNovelai from '../components/SettingsNovelai.vue'
import SettingsVaults from '../components/SettingsVaults.vue'
import '../styles/settings.css'

const props = defineProps<{ section: SettingsSection }>()
const emit = defineEmits<{ section: [SettingsSection] }>()

const title = computed(() => SETTINGS_SECTIONS.find((s) => s.id === props.section)?.title)
// Settings lives under KeepAlive: on screen between onActivated (also the first mount) and onDeactivated. Opening
// it re-reads the agent status — the poll sleeps while access is off (today's spend, a listener error).
const agent = useAgentStatus()
const visible = ref(false)
onActivated(() => { visible.value = true; agent.load() })
onDeactivated(() => (visible.value = false))
</script>

<template>
  <section class="settings stg">
    <div class="hd"><span class="crumb">Settings /</span><h2>{{ title }}</h2></div>
    <div class="body">
      <div v-show="section === 'general'" class="page"><SettingsGeneral /></div>
      <div v-show="section === 'novelai'" class="page"><SettingsNovelai /></div>
      <div v-show="section === 'vaults'" class="page"><SettingsVaults /></div>
      <div v-show="section === 'agents'" class="page"><SettingsAgents @open-activity="emit('section', 'activity')" /></div>
      <div v-show="section === 'activity'" class="page tall"><SettingsActivity :active="visible && section === 'activity'" /></div>
    </div>
  </section>
</template>

<style scoped>
.settings{flex:1;display:flex;flex-direction:column;min-width:0;background:var(--bg)}
.hd{display:flex;align-items:center;gap:10px;height:55px;padding:0 20px;border-bottom:1px solid var(--border);flex-shrink:0}
.hd h2{margin:0;font-size:18px;font-weight:700}
.crumb{font-size:18px;font-weight:600;color:var(--text-faint)}
.body{flex:1;min-height:0;padding:24px;overflow:auto}
.page{max-width:680px;display:flex;flex-direction:column;gap:18px}
.page.tall{height:100%}
</style>
