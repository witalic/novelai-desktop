/* Vault autosave (extracted from CanvasBoard): a dirty-flagged work persisted at meaningful moments —
 * leaving Generate, app close, and a periodic timer — never on every keystroke, so heavy works don't save
 * every few seconds. Owns the save state + logic; the component wires the lifecycle (listeners, hooks). */
import { nextTick, ref, type Ref } from 'vue'
import { ApiError, getAppSettings, saveWork } from '../api'
import { canvasToWork } from '../vault/serialize'
import { newId } from '../vault/ids'
import { useToast } from './useToast'
import { useVault, vaultMayBeAway } from './useVault'
import { catchUpQueue, type AgentOp } from '../vault/agentOps'
import type { GenResult, PanelParams, PersistedCombo, WorkDoc } from '../types'

export type SaveState = 'idle' | 'dirty' | 'saving' | 'saved'

/* eslint-disable @typescript-eslint/no-explicit-any */
interface Deps {
  nodes: Ref<any[]>
  viewport: Ref<any>
  params: () => PanelParams
  drafts: () => GenResult[]
  favorites: () => string[] // per-work quick-access set (Library block ids) — persisted with the work
  combos: () => PersistedCombo[] // saved block sets — persisted with the work
  onNoVault: () => void // no vault configured → route the user to Settings
  onSaved?: (workId: string) => void // after a successful save: swap just-persisted data: URLs for vault URLs
  onConflict?: () => void // a concurrent edit was detected (409) → the host reloads the work
  // The stored work `id` is no longer in the vault (404: deleted meanwhile, or the vault changed under it). The host
  // keeps what isn't saved as a new work — a new id, no base — and the save goes on as that one.
  onGone?: (id: string) => void
  // What agents changed in the stored copy since `since` (null: it was also saved some other way) …
  agentChanges?: (workId: string, since: string) => Promise<{ at: string; ops: AgentOp[] } | null>
  applyAgentOps?: (ops: AgentOp[]) => void // … and how the host replays those edits over the canvas
}

export function useAutosave({
  nodes, viewport, params, drafts, favorites, combos, onNoVault, onSaved, onConflict, onGone, agentChanges, applyAgentOps,
}: Deps) {
  const toast = useToast()
  const { unreachable: vaultAway } = useVault()
  const title = ref('')
  const cover = ref<string | null>(null) // the chosen cover image (canvasToWork falls back to the first gallery image)
  const vaultReady = ref(false)
  const workId = ref(newId('work'))
  const baseUpdatedAt = ref('') // updated_at the canvas loaded — sent as the optimistic-lock base (H3)
  const saveState = ref<SaveState>('idle')
  const savedAt = ref('')

  let dirty = false
  let inflight: Promise<boolean> | null = null // the current save (if any), so callers can join + await it
  let pendingResave = false
  let lastSig = ''
  let ignoreDirty = false // suppress markDirty while onSaved rewrites persisted urls (not a user edit)

  // A work is worth persisting once it has a title, ANY kept image, ANY non-empty prompt block — in a
  // zone or loose: scratch persists too (zones define role, not survival), so a scratch-only work must
  // not be discarded silently on leave — or ANY saved combination.
  function isMeaningful() {
    return title.value.trim().length > 0 || combos().length > 0 || nodes.value.some((n) =>
      n.type === 'image'
      || (n.type === 'block' && String(n.data?.text || '').trim().length > 0))
  }

  // The WorkDoc both save paths (autosave + the close beacon) write — one builder so they can't drift.
  function buildDoc(): WorkDoc {
    const doc = canvasToWork(nodes.value, viewport.value, params(), { id: workId.value, title: title.value, cover: cover.value },
      drafts(), favorites(), combos())
    doc.updated_at = baseUpdatedAt.value // optimistic-lock base — the server 409s if the stored copy is newer
    return doc
  }

  // Cheap change signature (excludes image *bytes*, keeps their domain fields) so an unchanged work is
  // never re-written, but favourite/tags/group/description edits on an image DO trigger a save (H1).
  function changeKey(): string {
    const sig = nodes.value.map((n) => ({
      i: n.id, p: n.parentNode ?? null,
      x: Math.round(n.position?.x ?? 0), y: Math.round(n.position?.y ?? 0),
      s: n.style,
      d: n.type === 'image'
        ? { u: n.data?.url ? 1 : 0, f: !!n.data?.favorite, t: n.data?.tags, g: n.data?.group ?? null, ds: n.data?.description ?? '' }
        : n.data,
    }))
    return JSON.stringify({ t: title.value, cv: cover.value, params: params(), nodes: sig, stack: drafts().map((d) => d.id), favs: favorites(), combos: combos() })
  }

  function markDirty() {
    if (ignoreDirty || !isMeaningful()) return
    dirty = true
    if (inflight) pendingResave = true // edit landed mid-save → the in-flight run re-saves it (see flush)
    if (saveState.value !== 'saving') saveState.value = 'dirty'
  }

  // An agent edited the stored copy (backend app/vault/worklog): replay its edits over the canvas — our unsaved
  // edits stay — and take the stored stamp as the new lock base, so the next save doesn't 409. True when the base
  // moved on; false when there was nothing to replay or the stored copy also got there some other way (only the
  // 409 reload catches up then). One catch-up at a time (catchUpQueue).
  const catchUp = catchUpQueue(runCatchUp)
  async function runCatchUp(): Promise<boolean> {
    if (!agentChanges || !applyAgentOps) return false
    const wid = workId.value
    for (let tries = 0; tries < 3; tries++) {
      const since = baseUpdatedAt.value
      if (!since) return false // never saved — there's no stored copy to catch up with
      let res: Awaited<ReturnType<typeof agentChanges>>
      try { res = await agentChanges(wid, since) } catch { return false }
      if (workId.value !== wid) return false // another work was opened meanwhile
      if (baseUpdatedAt.value !== since) continue // one of our saves landed meanwhile → ask again from its stamp
      if (!res) return false
      if (res.ops.length) {
        const wasDirty = dirty
        ignoreDirty = true
        applyAgentOps(res.ops)
        await nextTick() // the deep watcher fires inside the suppression
        ignoreDirty = false
        if (!wasDirty) lastSig = changeKey() // nothing of ours was unsaved → the canvas now IS the stored copy
      }
      baseUpdatedAt.value = res.at
      return res.at !== since
    }
    return false
  }
  // An agent notice for this work: let a running save land (or 409 and catch up itself) first.
  async function onAgentEdit() {
    if (inflight) await inflight
    await catchUp()
  }
  // A running save has landed (or met its answer), so the canvas is what's stored — or knows it isn't.
  async function settle() { if (inflight) await inflight }
  // Whether the canvas holds anything the stored copy doesn't.
  const hasUnsaved = () => isMeaningful() && changeKey() !== lastSig

  // One save attempt. Returns true if the work is persisted (or nothing needed saving), false on error.
  // `conflicts`: 409s already answered by catching up — a busy agent can land more edits in between.
  async function doSave(conflicts = 0): Promise<boolean> {
    const key = changeKey()
    if (key === lastSig) { dirty = false; if (saveState.value === 'dirty') saveState.value = 'saved'; return true }
    // The vault can't be reached: don't ship the whole work (images and all) just to hear so — it waits for it.
    if (vaultAway.value) { dirty = true; saveState.value = 'dirty'; return false }
    saveState.value = 'saving'
    const wid = workId.value
    try {
      const res = await saveWork(buildDoc())
      if (workId.value !== wid) return true // the canvas moved on meanwhile (another work, or this one let go)
      baseUpdatedAt.value = res.updated_at // new base for the next save
      lastSig = key
      dirty = false
      saveState.value = 'saved'
      savedAt.value = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      // Swap just-persisted data: URLs for vault URLs so the next save doesn't re-serialize their base64.
      // changeKey treats url as present/absent (not by value), so this never marks the work dirty; the
      // suppression flag stops the deep watcher's mutation from doing so during the microtask.
      if (onSaved) { ignoreDirty = true; onSaved(workId.value); await nextTick(); ignoreDirty = false }
      return true
    } catch (e) {
      if (workId.value !== wid) return true // an answer about a work the canvas has moved on from
      // Stored copy is newer: when only an agent changed it, replay its edits and save again on the new base.
      if (e instanceof ApiError && e.status === 409 && conflicts < 5 && await catchUp()) return doSave(conflicts + 1)
      if (e instanceof ApiError && e.status === 409) { // concurrent edit → let the host reload; don't stay dirty
        saveState.value = 'idle'
        toast.push('This work changed elsewhere — reloading.', 'err')
        onConflict?.()
        return false
      }
      if (e instanceof ApiError && e.status === 404 && onGone && conflicts < 5) { // gone from the vault while open
        onGone(wid) // what isn't saved stays, as a new work (no base: it can't be "gone" again) …
        return isMeaningful() ? doSave(conflicts + 1) : true // … and is saved as that
      }
      if (vaultMayBeAway(e)) { // the vault can't be reached: the save waits for it — the strip says why, no toast
        dirty = true
        saveState.value = 'dirty'
        return false
      }
      dirty = true
      saveState.value = 'dirty'
      toast.push(e instanceof Error ? e.message : 'Save failed', 'err')
      return false
    }
  }

  // Persist if needed. Concurrent triggers coalesce onto ONE in-flight save that re-runs for edits which
  // land mid-save, and the promise resolves only once the save has actually finished — so callers (New work,
  // quit, leave) can await it and learn whether it succeeded.
  async function flush(force = false): Promise<boolean> {
    if (!isMeaningful()) return true          // nothing worth saving → "ok"
    if (!vaultReady.value) return false        // meaningful content but no vault → NOT persisted (New work confirms)
    if (inflight) { pendingResave = true; return inflight } // join the running save (and make it re-run)
    if (!force && !dirty) return true
    const run = (async () => {
      let ok = true
      do {
        pendingResave = false
        ok = await doSave()
      } while (ok && pendingResave) // re-save edits that arrived while the previous attempt was in flight
      return ok
    })()
    inflight = run
    try { return await run } finally { inflight = null }
  }
  const flushIfDirty = () => flush() // force=false → no-op unless dirty

  // Manual save (button / ⌘S). With no vault, route the user to Settings.
  function manualSave() {
    if (!vaultReady.value) { onNoVault(); return }
    flush(true)
  }

  // Browser fallback for window close (Electron uses onBeforeQuit); best-effort, size-limited.
  function onBeforeUnload() {
    if (!vaultReady.value || !dirty || inflight || !isMeaningful() || changeKey() === lastSig) return
    // The lock base rides along, so the beacon still yields to a concurrent edit.
    try { navigator.sendBeacon('/api/vault/works', new Blob([JSON.stringify(buildDoc())], { type: 'application/json' })) } catch { /* best-effort */ }
    // The beacon bumps the stored stamp but returns nothing, so our base is now stale. If the close is
    // cancelled, opt the surviving session out of the lock on its next save (empty base) rather than let
    // it 409 against its own beacon and reload away later edits (#2).
    baseUpdatedAt.value = ''
  }

  // Periodic auto-save; interval comes from Settings and is re-read when returning to Generate.
  let autosaveTimer: ReturnType<typeof setInterval> | null = null
  async function refreshInterval() {
    let ms = 300_000
    try { ms = Math.max(30, (await getAppSettings()).autosave_interval_s) * 1000 } catch { /* keep default */ }
    if (autosaveTimer) clearInterval(autosaveTimer)
    autosaveTimer = setInterval(flushIfDirty, ms)
  }
  const stopAutosave = () => { if (autosaveTimer) clearInterval(autosaveTimer) }

  // Adopt the current canvas as the saved baseline (after New work / opening a work) — not dirty.
  function resetBaseline(state: SaveState) {
    lastSig = changeKey()
    dirty = false
    saveState.value = state
    savedAt.value = ''
  }

  return {
    title, cover, vaultReady, workId, baseUpdatedAt, saveState, savedAt,
    changeKey, markDirty, flush, flushIfDirty, manualSave, onBeforeUnload, refreshInterval, stopAutosave, resetBaseline,
    onAgentEdit, settle, hasUnsaved,
  }
}
