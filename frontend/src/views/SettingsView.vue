<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useTheme } from '../composables/useTheme'
import { useToast } from '../composables/useToast'
import { useConfirm } from '../composables/useConfirm'
import {
  addVault, clearNovelaiToken, deleteVault, getAppSettings, getTokenStatus, getVaultConfig,
  moveStatus, patchAppSettings, setActiveVault, setNovelaiToken, startMoveVault, type VaultConfig,
} from '../api'
import { browseFolder, openInFileManager } from '../electron'

const { theme, accent, accents } = useTheme()
const { push } = useToast()
const { confirm } = useConfirm()

const cfg = ref<VaultConfig | null>(null)
const busy = ref(false)

const interval = ref(300)
const INTERVALS = [{ v: 60, l: '1 min' }, { v: 300, l: '5 min' }, { v: 600, l: '10 min' }, { v: 1800, l: '30 min' }]

const moving = ref(false)
const movingDir = ref<string | null>(null)
const moveProg = ref({ done: 0, total: 0 })
let movePoll: ReturnType<typeof setInterval> | null = null

const tokenSet = ref(false)
const tokenInput = ref('')
const tokenBusy = ref(false)

async function loadCfg() {
  try { cfg.value = await getVaultConfig() } catch (e) { push(e instanceof Error ? e.message : 'Failed to load vaults', 'err') }
}
onMounted(async () => {
  await loadCfg()
  try { interval.value = (await getAppSettings()).autosave_interval_s } catch { /* keep default */ }
  try { tokenSet.value = (await getTokenStatus()).set } catch { /* keep default */ }
})

async function saveToken() {
  const t = tokenInput.value.trim()
  if (!t || tokenBusy.value) return
  tokenBusy.value = true
  try { tokenSet.value = (await setNovelaiToken(t)).set; tokenInput.value = ''; push('NovelAI token saved', 'ok') }
  catch (e) { push(e instanceof Error ? e.message : 'Could not save token', 'err') }
  finally { tokenBusy.value = false }
}
async function clearToken() {
  const ok = await confirm({
    title: 'Remove NovelAI token',
    message: 'Remove the stored token? Generation will fall back to the offline mock until you set it again.',
    confirmLabel: 'Remove', danger: true,
  })
  if (!ok) return
  tokenBusy.value = true
  try { tokenSet.value = (await clearNovelaiToken()).set; push('Token removed', 'ok') }
  catch (e) { push(e instanceof Error ? e.message : 'Could not remove token', 'err') }
  finally { tokenBusy.value = false }
}
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
  movingDir.value = dir
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
          movingDir.value = null
          if (st.error) push(`Move failed: ${st.error}`, 'err')
          else { push('Vault moved', 'ok'); await loadCfg() }
        }
      } catch { /* transient poll error — keep trying */ }
    }, 400)
  } catch (e) {
    moving.value = false
    movingDir.value = null
    push(e instanceof Error ? e.message : 'Could not start move', 'err')
  }
}
async function openFolder(dir: string) {
  await openInFileManager(dir)
}
async function changeInterval(v: number) {
  interval.value = v
  try { await patchAppSettings({ autosave_interval_s: v }) }
  catch (e) { push(e instanceof Error ? e.message : 'Could not save', 'err') }
}
</script>

<template>
  <section class="settings">
    <div class="hd"><h2>Settings</h2></div>
    <div class="body">
      <!-- NovelAI access -->
      <div class="card">
        <div class="card-hd">NovelAI access</div>
        <div class="setting col">
          <div class="txt">
            <div class="name">API token</div>
            <div class="desc">Your NovelAI persistent token (Account → Get Persistent API Token). Stored in the OS keychain — never in files, logs or git.</div>
          </div>
          <div class="tstatus" :class="{ on: tokenSet }">
            {{ tokenSet ? '✓ Token set' : 'Not set — generation uses the offline mock' }}
          </div>
          <div class="inrow">
            <input class="tin" type="password" v-model="tokenInput" placeholder="Paste token to set or replace…"
              autocomplete="off" @keyup.enter="saveToken" />
            <button class="savebtn" :disabled="tokenBusy || !tokenInput.trim()" @click="saveToken">Save</button>
            <button v-if="tokenSet" class="ghostbtn" :disabled="tokenBusy" @click="clearToken">Clear</button>
          </div>
        </div>
      </div>

      <!-- Vaults -->
      <div class="card">
        <div class="card-hd">Vaults</div>
        <div class="setting col">
          <div class="txt">
            <div class="name">Your vaults</div>
            <div class="desc">Files on disk — works, images and the prompt library. Pick an existing vault folder or a new one; switch, move or delete.</div>
          </div>

          <div class="vaults">
            <div v-for="v in cfg?.vaults || []" :key="v.dir" class="vrow" :class="{ active: v.active }">
              <button class="radio" :class="{ on: v.active }" :disabled="v.active || busy || moving" @click="switchTo(v.dir)" title="Make active">
                <span></span>
              </button>
              <div class="vinfo">
                <div class="vpath">{{ v.dir }}</div>
                <div class="vmeta">
                  <span v-if="v.active" class="tag">active</span>
                  <span v-if="!v.writable" class="warn">read-only</span>
                </div>
              </div>
              <div class="vactions">
                <button class="vbtn" :disabled="busy || moving" title="Open in file manager" @click="openFolder(v.dir)">📂</button>
                <button class="vbtn" :disabled="busy || moving" title="Move to another folder" @click="moveVaultRow(v.dir)">➜</button>
                <button class="vbtn danger" :disabled="busy || moving" title="Delete permanently" @click="removeVault(v.dir)">🗑</button>
              </div>
            </div>
          </div>

          <div v-if="moving" class="progress">
            <div class="bar"><div class="fill" :style="{ width: (moveProg.total ? Math.round(100 * moveProg.done / moveProg.total) : 0) + '%' }"></div></div>
            <span class="pct">Moving… {{ moveProg.done }} / {{ moveProg.total || '…' }} files</span>
          </div>

          <button class="addbtn" :disabled="busy || moving" @click="addFolder"><span>＋</span> Add vault</button>
        </div>
      </div>

      <!-- Auto-save -->
      <div class="card">
        <div class="card-hd">Auto-save</div>
        <div class="setting">
          <div class="txt">
            <div class="name">Interval</div>
            <div class="desc">Works also save when you leave the Generate tab and when the app closes.</div>
          </div>
          <div class="seg int-seg">
            <button v-for="i in INTERVALS" :key="i.v" :class="{ active: interval === i.v }" @click="changeInterval(i.v)">{{ i.l }}</button>
          </div>
        </div>
      </div>

      <!-- Appearance -->
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
.hd{display:flex;align-items:center;height:55px;padding:0 20px;border-bottom:1px solid var(--border);flex-shrink:0}
.hd h2{margin:0;font-size:18px;font-weight:700}
.body{padding:24px;overflow:auto;display:flex;flex-direction:column;gap:18px}
.card{max-width:620px;background:var(--surface-1);border:1px solid var(--border);border-radius:var(--radius-lg)}
.card-hd{padding:14px 18px;border-bottom:1px solid var(--border);font-weight:600}
.setting{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px 18px}
.setting + .setting{border-top:1px solid var(--border)}
.setting.col{flex-direction:column;align-items:stretch;gap:12px}
.txt .name{font-weight:500}
.txt .desc{font-size:12px;color:var(--text-dim);margin-top:2px}

.vaults{display:flex;flex-direction:column;gap:8px}
.vrow{display:flex;align-items:center;gap:12px;padding:10px 12px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius)}
.vrow.active{border-color:color-mix(in srgb,var(--accent) 45%,var(--border))}
.radio{flex-shrink:0;width:18px;height:18px;border-radius:50%;border:2px solid var(--border-strong);background:transparent;cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0}
.radio.on{border-color:var(--accent)}
.radio.on span{width:8px;height:8px;border-radius:50%;background:var(--accent)}
.radio:disabled{cursor:default}
.vinfo{flex:1;min-width:0}
.vpath{font-size:13px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.vmeta{display:flex;gap:6px;margin-top:3px;min-height:14px}
.vmeta .tag{font-size:10px;font-weight:600;color:var(--accent);background:var(--nav-active);border-radius:20px;padding:1px 8px}
.vmeta .warn{font-size:10px;font-weight:600;color:#e8913a}
.vactions{display:flex;gap:6px;flex-shrink:0}
.vbtn{width:32px;height:32px;flex-shrink:0;display:flex;align-items:center;justify-content:center;border:1px solid var(--border);background:var(--surface-1);color:var(--text-dim);border-radius:var(--radius);cursor:pointer;font-size:14px;line-height:1}
.vbtn:hover{color:var(--text);border-color:var(--border-strong)}
.vbtn.danger:hover{color:#e2483d;border-color:color-mix(in srgb,#e2483d 45%,var(--border))}
.vbtn:disabled{opacity:.45;cursor:default}

.tstatus{font-size:12px;font-weight:600;color:var(--text-faint)}
.tstatus.on{color:#3aa675}
.inrow{display:flex;gap:8px}
.tin{flex:1;font:inherit;font-size:13px;color:var(--text);background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px;outline:none}
.tin:focus{border-color:var(--accent)}
.savebtn{flex-shrink:0;border:0;background:var(--accent);color:var(--on-accent);border-radius:var(--radius);font-weight:600;font-size:13px;padding:0 16px;cursor:pointer}
.savebtn:disabled{opacity:.5;cursor:default}
.ghostbtn{flex-shrink:0;border:1px solid var(--border);background:var(--surface-2);color:var(--text-dim);border-radius:var(--radius);font-weight:600;font-size:13px;padding:8px 14px;cursor:pointer}
.ghostbtn:hover{color:var(--text);border-color:var(--border-strong)}
.ghostbtn:disabled{opacity:.5;cursor:default}

.progress{display:flex;align-items:center;gap:10px}
.bar{flex:1;height:6px;background:var(--surface-3);border-radius:20px;overflow:hidden}
.fill{height:100%;background:var(--accent);transition:width .2s}
.pct{font-size:12px;color:var(--text-dim);white-space:nowrap}

.addbtn{align-self:flex-start;display:flex;align-items:center;gap:7px;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text);border-radius:var(--radius);font-size:13px;font-weight:600;padding:8px 14px;cursor:pointer}
.addbtn:hover{border-color:var(--accent);color:var(--accent)}
.addbtn:disabled{opacity:.5;cursor:default}

.seg{display:flex;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.seg button{border:0;background:transparent;color:var(--text-dim);font:inherit;font-size:12px;font-weight:600;padding:8px 12px;cursor:pointer}
.seg button.active{background:var(--accent);color:var(--on-accent)}
.theme-seg{width:180px}
.theme-seg button{flex:1;padding:8px 4px}
.int-seg button + button{border-left:1px solid var(--border)}
.swatches{display:flex;gap:8px}
.sw{width:24px;height:24px;border-radius:50%;cursor:pointer;border:2px solid transparent;padding:0}
.sw.active{border-color:var(--text-dim)}
</style>
