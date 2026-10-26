/* Whether the active vault can be reached (design/vault-unavailable.html). A vault on a network share or a drive can
 * drop away for a while; the app never switches to another one by itself — it says so, keeps what's open, and goes on
 * once the folder answers again. Singleton module state: checked at start, whenever a vault call fails with a 503,
 * and every 20 s while the vault is away.
 *
 * `onBack` is for what waited (a save): it runs when the vault that was away — or that a call just failed against —
 * answers again. Never on a switch to another vault: what waited belongs to the one it was meant for. */
import { ref } from 'vue'
import { ApiError, getVaultConfig, type VaultInfo } from '../api'

const RETRY_MS = 20_000
const unreachable = ref<string | null>(null) // the active vault's folder, while it can't be reached
const checking = ref(false)
const backListeners = new Set<() => void>()
let timer: ReturnType<typeof setInterval> | null = null
let lastActive: string | null = null // the active vault as the last check found it
let suspect: string | null = null // the vault a call failed against (503), until a check answers
let again = false // a check was asked for while one ran — that one may have asked before the call failed

async function check(): Promise<void> {
  if (checking.value) { again = true; return }
  checking.value = true
  try {
    settle((await getVaultConfig()).vaults.find((v) => v.active) ?? null)
  } catch { /* the backend isn't answering — that's not the vault's state; leave it as it was */ } finally {
    checking.value = false
    if (again) { again = false; void check() }
  }
}

function settle(active: VaultInfo | null) {
  const away = active && !active.reachable ? active.dir : null
  const waited = unreachable.value ?? suspect // the vault something waits for, if any
  suspect = null
  lastActive = active?.dir ?? null
  unreachable.value = away
  if (away && !timer) timer = setInterval(check, RETRY_MS)
  if (!away && timer) { clearInterval(timer); timer = null }
  if (waited && !away && active?.dir === waited) for (const fn of [...backListeners]) fn()
}

/** Whether a failed call may be the vault going away — a 503 makes the app check (and, should the vault answer
 *  after all, what waited goes on). */
export function vaultMayBeAway(e: unknown): boolean {
  if (!(e instanceof ApiError) || e.status !== 503) return false
  suspect = unreachable.value ?? lastActive
  void check()
  return true
}

export function useVault() {
  return {
    unreachable, checking, check,
    // Run `fn` when what waited may go on: the same vault answers again (a save retries, a list reloads).
    onBack: (fn: () => void) => { backListeners.add(fn); return () => backListeners.delete(fn) },
  }
}
