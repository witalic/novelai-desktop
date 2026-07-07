<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useTheme } from '../composables/useTheme'
import { useToast } from '../composables/useToast'
import { getVaultConfig, setVaultConfig, type VaultConfig } from '../api'

const { theme, accent, accents } = useTheme()
const { push } = useToast()

const vault = ref<VaultConfig | null>(null)
const vaultInput = ref('')
const saving = ref(false)

onMounted(async () => {
  try {
    vault.value = await getVaultConfig()
    vaultInput.value = vault.value.vault_dir || vault.value.proposed_default
  } catch { /* backend not ready */ }
})

async function useFolder() {
  if (!vaultInput.value.trim() || saving.value) return
  saving.value = true
  try {
    vault.value = await setVaultConfig(vaultInput.value.trim())
    push('Vault folder set', 'ok')
  } catch (e) {
    push(e instanceof Error ? e.message : 'Could not set folder', 'err')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section class="settings">
    <div class="hd"><h2>Settings</h2></div>
    <div class="body">
      <div class="card">
        <div class="card-hd">Vault</div>
        <div class="setting col">
          <div class="txt">
            <div class="name">Vault folder</div>
            <div class="desc">Where your works, images and library live on disk. Pick any folder (Obsidian-style).</div>
          </div>
          <div class="vaultrow">
            <input class="path" v-model="vaultInput" placeholder="C:\Users\…\novelai-vault" />
            <button class="use" :disabled="saving" @click="useFolder">{{ saving ? 'Setting…' : 'Use folder' }}</button>
          </div>
          <div class="status" v-if="vault">
            <span v-if="vault.initialized" class="ok">✓ Vault ready — {{ vault.vault_dir }}</span>
            <span v-else class="muted">No vault yet. Suggested: {{ vault.proposed_default }}</span>
          </div>
        </div>
      </div>

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
    </div>
  </section>
</template>

<style scoped>
.settings{flex:1;display:flex;flex-direction:column;min-width:0;background:var(--bg)}
.hd{padding:15px 20px;border-bottom:1px solid var(--border);flex-shrink:0}
.hd h2{margin:0;font-size:15px;font-weight:600}
.body{padding:24px;overflow:auto;display:flex;flex-direction:column;gap:18px}
.card{max-width:560px;background:var(--surface-1);border:1px solid var(--border);border-radius:var(--radius-lg)}
.card-hd{padding:14px 18px;border-bottom:1px solid var(--border);font-weight:600}
.setting{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px 18px}
.setting + .setting{border-top:1px solid var(--border)}
.setting.col{flex-direction:column;align-items:stretch;gap:10px}
.txt .name{font-weight:500}
.txt .desc{font-size:12px;color:var(--text-dim);margin-top:2px}
.vaultrow{display:flex;gap:8px}
.path{flex:1;font:inherit;font-size:13px;color:var(--text);background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px;outline:none}
.path:focus{border-color:var(--accent)}
.use{flex-shrink:0;border:0;background:var(--accent);color:var(--on-accent);border-radius:var(--radius);font-weight:600;font-size:13px;padding:0 16px;cursor:pointer}
.use:disabled{opacity:.6;cursor:default}
.status{font-size:12px}
.status .ok{color:#3aa675}
.status .muted{color:var(--text-dim)}
.theme-seg{width:180px}
.theme-seg button{padding:8px 4px}
.swatches{display:flex;gap:8px}
.sw{width:24px;height:24px;border-radius:50%;cursor:pointer;border:2px solid transparent;padding:0}
.sw.active{border-color:var(--text-dim)}
</style>
