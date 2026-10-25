<script setup lang="ts">
/* Settings → Vaults: the vault folders on disk — add one, make one active, open, move or delete it. */
import { onMounted, onUnmounted, ref } from 'vue'
import {
  addVault, deleteVault, getVaultConfig, moveStatus, setActiveVault, startMoveVault, type VaultConfig,
} from '../api'
import { useConfirm } from '../composables/useConfirm'
import { useToast } from '../composables/useToast'
import { browseFolder, openInFileManager } from '../electron'

const { push } = useToast()
const { confirm } = useConfirm()

const cfg = ref<VaultConfig | null>(null)
const busy = ref(false)
const moving = ref(false)
const moveProg = ref({ done: 0, total: 0 })
let movePoll: ReturnType<typeof setInterval> | null = null

async function loadCfg() {
  try { cfg.value = await getVaultConfig() } catch (e) { push(e instanceof Error ? e.message : 'Failed to load vaults', 'err') }
}
onMounted(loadCfg)
onUnmounted(() => { if (movePoll) clearInterval(movePoll) })

async function addFolder() {
  if (busy.value || moving.value) return
  const dir = await browseFolder('Choose a vault folder')
  if (!dir) return
  busy.value = true
  try { cfg.value = await addVault(dir); push('Vault added and activated', 'ok') }
  catch (e) { push(e instanceof Error ? e.message : 'Could not add vault', 'err') }
  finally { busy.value = false }
}
async function switchTo(dir: string) {
  if (busy.value || moving.value) return
  busy.value = true
  try { cfg.value = await setActiveVault(dir); push('Switched active vault', 'ok') }
  catch (e) { push(e instanceof Error ? e.message : 'Could not switch', 'err') }
  finally { busy.value = false }
}
async function removeVault(dir: string) {
  const ok = await confirm({
    title: 'Delete vault permanently',
    message: `Permanently delete this vault folder and ALL its works, images and blocks from disk?\n\n${dir}\n\nThis cannot be undone.`,
    confirmLabel: 'Delete forever', danger: true,
  })
  if (!ok) return
  busy.value = true
  try { cfg.value = await deleteVault(dir); push('Vault deleted', 'ok') }
  catch (e) { push(e instanceof Error ? e.message : 'Could not delete', 'err') }
  finally { busy.value = false }
}
async function moveVaultRow(dir: string) {
  if (moving.value || busy.value) return
  const dst = await browseFolder('Choose the new (empty) location')
  if (!dst) return
  const ok = await confirm({
    title: 'Move vault',
    message: `Move this vault to a new location? All files are copied there and the original folder is removed.\n\nFrom: ${dir}\nTo: ${dst}`,
    confirmLabel: 'Move',
  })
  if (!ok) return
  moving.value = true
  moveProg.value = { done: 0, total: 0 }
  try {
    await startMoveVault(dir, dst)
    movePoll = setInterval(async () => {
      try {
        const st = await moveStatus()
        moveProg.value = { done: st.done, total: st.total }
        if (!st.active) {
          if (movePoll) clearInterval(movePoll)
          movePoll = null
          moving.value = false
          if (st.error) push(`Move failed: ${st.error}`, 'err')
          else { push('Vault moved', 'ok'); await loadCfg() }
        }
      } catch { /* transient poll error — keep trying */ }
    }, 400)
  } catch (e) {
    moving.value = false
    push(e instanceof Error ? e.message : 'Could not start move', 'err')
  }
}
</script>

<template>
  <div class="card">
    <div class="setting col">
      <div class="txt"><div class="desc">Files on disk — works, images and the prompt library. Add an existing vault folder or a new
        one; switch, move or delete.</div></div>

      <div class="vaults">
        <div v-for="v in cfg?.vaults || []" :key="v.dir" class="vrow" :class="{ active: v.active }">
          <div class="vinfo">
            <div class="vpath" :title="v.dir">{{ v.dir }}</div>
            <div class="vmeta">
              <span v-if="v.active" class="tag">Active</span>
              <span v-if="!v.writable" class="warn">read-only</span>
            </div>
          </div>
          <button v-if="!v.active" class="gbtn" :disabled="busy || moving" @click="switchTo(v.dir)">Make active</button>
          <button class="gbtn" :disabled="busy || moving" @click="openInFileManager(v.dir)">Open</button>
          <button class="gbtn" :disabled="busy || moving" @click="moveVaultRow(v.dir)">Move…</button>
          <button class="del" :disabled="busy || moving" title="Delete permanently" @click="removeVault(v.dir)">🗑</button>
        </div>
      </div>

      <div v-if="moving" class="progress">
        <div class="bar"><div class="barfill" :style="{ width: (moveProg.total ? Math.round(100 * moveProg.done / moveProg.total) : 0) + '%' }"></div></div>
        <span class="pct">Moving… {{ moveProg.done }} / {{ moveProg.total || '…' }} files</span>
      </div>

      <button class="gbtn add" :disabled="busy || moving" @click="addFolder">＋ Add vault</button>
    </div>
  </div>
</template>

<style scoped>
.vaults{display:flex;flex-direction:column;gap:8px}
.vrow{display:flex;align-items:center;gap:8px;padding:10px 12px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius)}
.vrow.active{border-color:color-mix(in srgb,var(--accent) 45%,var(--border))}
.vrow .gbtn{background:var(--surface-1)}
.vinfo{flex:1;min-width:0}
.vpath{font-size:13px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.vmeta{display:flex;gap:6px;margin-top:3px;min-height:14px}
.vmeta .tag{font-size:10px;font-weight:700;color:var(--accent);background:var(--nav-active);border-radius:20px;padding:1px 8px}
.vmeta .warn{font-size:10px;font-weight:700;color:var(--warn-fg);border:1px solid color-mix(in srgb,var(--warn-fg) 40%,var(--border));
  border-radius:20px;padding:0 7px}
.del{width:30px;height:30px;flex-shrink:0;display:flex;align-items:center;justify-content:center;border:1px solid var(--border);
  background:var(--surface-1);color:var(--text-dim);border-radius:var(--radius);cursor:pointer;font-size:13px;line-height:1}
.del:hover:not(:disabled){color:var(--danger);border-color:color-mix(in srgb,var(--danger) 45%,var(--border))}
.del:disabled{opacity:.45;cursor:default}
.progress{display:flex;align-items:center;gap:10px}
.bar{flex:1;height:6px;background:var(--surface-3);border-radius:20px;overflow:hidden}
.barfill{height:100%;background:var(--accent);transition:width .2s}
.pct{font-size:12px;color:var(--text-dim);white-space:nowrap}
.add{align-self:flex-start}
</style>
