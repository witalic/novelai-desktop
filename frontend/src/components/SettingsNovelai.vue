<script setup lang="ts">
/* Settings → NovelAI: the API token (OS keychain — the value never comes back, only whether it is set) and the
 * account it opens: tier, Anlas, and what generates for free. */
import { computed, onMounted, ref } from 'vue'
import { clearNovelaiToken, getTokenStatus, setNovelaiToken } from '../api'
import { useAccount } from '../composables/useAccount'
import { useConfirm } from '../composables/useConfirm'
import { useToast } from '../composables/useToast'

const { push } = useToast()
const { confirm } = useConfirm()
const { subscription, refresh } = useAccount()

const tokenSet = ref(false)
const tokenInput = ref('')
const busy = ref(false)
onMounted(async () => {
  try { tokenSet.value = (await getTokenStatus()).set } catch { /* keep default */ }
})

// Opus (tier 3, active) generates small images for free — the same rule the generate button and agents follow.
const freeText = computed(() => (subscription.value?.active && subscription.value.tier >= 3
  ? 'Up to 1024×1024 px, ≤ 28 steps, 1 image' : 'None — every generation costs Anlas'))

async function saveToken() {
  const t = tokenInput.value.trim()
  if (!t || busy.value) return
  busy.value = true
  try { tokenSet.value = (await setNovelaiToken(t)).set; tokenInput.value = ''; push('NovelAI token saved', 'ok'); refresh() }
  catch (e) { push(e instanceof Error ? e.message : 'Could not save token', 'err') }
  finally { busy.value = false }
}
async function clearToken() {
  const ok = await confirm({
    title: 'Remove NovelAI token',
    message: 'Remove the stored token? Generation will fall back to the offline mock until you set it again.',
    confirmLabel: 'Remove', danger: true,
  })
  if (!ok) return
  busy.value = true
  try { tokenSet.value = (await clearNovelaiToken()).set; push('Token removed', 'ok') }
  catch (e) { push(e instanceof Error ? e.message : 'Could not remove token', 'err') }
  finally { busy.value = false }
}
</script>

<template>
  <div class="card">
    <div class="card-hd">API token<span class="sp"></span>
      <span class="tstatus" :class="{ on: tokenSet }">{{ tokenSet ? '✓ Token set' : 'Not set — generation uses the offline mock' }}</span></div>
    <div class="setting col">
      <div class="txt"><div class="desc">Your NovelAI persistent token (Account → Get Persistent API Token). Stored in the OS keychain —
        never in files, logs or git.</div></div>
      <div class="row">
        <input class="tin" type="password" v-model="tokenInput" placeholder="Paste token to set or replace…"
          autocomplete="off" @keyup.enter="saveToken" />
        <button class="pbtn" :disabled="busy || !tokenInput.trim()" @click="saveToken">Save</button>
        <button v-if="tokenSet" class="gbtn" :disabled="busy" @click="clearToken">Clear</button>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-hd">Account<span class="sp"></span><button class="gbtn" :disabled="!tokenSet" @click="refresh">Refresh</button></div>
    <!-- without a token the offline mock answers — its made-up account is never shown as the user's -->
    <div v-if="tokenSet && subscription" class="kv">
      <span class="k">Subscription</span><span><span class="tier">{{ subscription.tier_name }}</span></span>
      <span class="k">Anlas balance</span><span><b>◆ {{ subscription.anlas.toLocaleString() }}</b></span>
      <span class="k">Free generations</span><span>{{ freeText }}</span>
    </div>
    <div v-else class="empty">{{ tokenSet ? "NovelAI can't be reached right now — try Refresh."
      : 'Set your token above to see your subscription and Anlas.' }}</div>
  </div>
</template>

<style scoped>
.tstatus{font-size:12px;font-weight:600;color:var(--text-faint)}
.tstatus.on{color:var(--ok)}
.kv{display:grid;grid-template-columns:150px 1fr;gap:10px 14px;padding:16px 18px;font-size:13px;align-items:center}
.k{color:var(--text-dim);font-size:12.5px}
.tier{font-size:11px;font-weight:700;color:var(--accent);background:var(--nav-active);border-radius:20px;padding:2px 9px}
</style>
