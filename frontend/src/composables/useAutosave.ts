/* Vault autosave (extracted from CanvasBoard): a dirty-flagged work persisted at meaningful moments —
 * leaving Generate, app close, and a periodic timer — never on every keystroke, so heavy works don't save
 * every few seconds. Owns the save state + logic; the component wires the lifecycle (listeners, hooks). */
import { nextTick, ref, type Ref } from 'vue'
import { getAppSettings, saveWork } from '../api'
import { canvasToWork, GALLERY, LIBRARY, STATION } from '../vault/serialize'
import { newId } from '../vault/ids'
import { useToast } from './useToast'
import type { GenResult, PanelParams, WorkDoc } from '../types'

export type SaveState = 'idle' | 'dirty' | 'saving' | 'saved'

/* eslint-disable @typescript-eslint/no-explicit-any */
interface Deps {
  nodes: Ref<any[]>
  viewport: Ref<any>
  params: () => PanelParams
  drafts: () => GenResult[]
  onNoVault: () => void // no vault configured → route the user to Settings
  onSaved?: (workId: string) => void // after a successful save: swap just-persisted data: URLs for vault URLs
}

export function useAutosave({ nodes, viewport, params, drafts, onNoVault, onSaved }: Deps) {
  const toast = useToast()
  const title = ref('')
  const vaultReady = ref(false)
  const workId = ref(newId('work'))
  const saveState = ref<SaveState>('idle')
  const savedAt = ref('')

  let dirty = false
  let inflight: Promise<boolean> | null = null // the current save (if any), so callers can join + await it
  let pendingResave = false
  let lastSig = ''
  let ignoreDirty = false // suppress markDirty while onSaved rewrites persisted urls (not a user edit)

  // A work is worth persisting once it has a title, a kept gallery image, OR a non-empty prompt block in the
  // station/library — otherwise an assembled-but-ungenerated composition would be discarded silently on leave.
  function isMeaningful() {
    return title.value.trim().length > 0 || nodes.value.some((n) =>
      (n.type === 'image' && n.parentNode === GALLERY)
      || (n.type === 'block' && (n.parentNode === STATION || n.parentNode === LIBRARY)
          && String(n.data?.text || '').trim().length > 0))
  }

  // Cheap change signature (excludes image bytes) so an unchanged work is never re-written.
  function changeKey(): string {
    const sig = nodes.value.map((n) => ({
      i: n.id, p: n.parentNode ?? null,
      x: Math.round(n.position?.x ?? 0), y: Math.round(n.position?.y ?? 0),
      s: n.style, d: n.type === 'image' ? (n.data?.url ? 1 : 0) : n.data,
    }))
    return JSON.stringify({ t: title.value, params: params(), nodes: sig, stack: drafts().map((d) => d.id) })
  }

  function markDirty() {
    if (ignoreDirty || !isMeaningful()) return
    dirty = true
    if (inflight) pendingResave = true // edit landed mid-save → the in-flight run re-saves it (see flush)
    if (saveState.value !== 'saving') saveState.value = 'dirty'
  }

  // One save attempt. Returns true if the work is persisted (or nothing needed saving), false on error.
  async function doSave(): Promise<boolean> {
    const key = changeKey()
    if (key === lastSig) { dirty = false; if (saveState.value === 'dirty') saveState.value = 'saved'; return true }
    saveState.value = 'saving'
    try {
      await saveWork(canvasToWork(nodes.value, viewport.value, params(), { id: workId.value, title: title.value }, drafts()) as WorkDoc)
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
    if (!vaultReady.value || !isMeaningful()) return true
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
    const doc = canvasToWork(nodes.value, viewport.value, params(), { id: workId.value, title: title.value }, drafts())
    try { navigator.sendBeacon('/api/vault/works', new Blob([JSON.stringify(doc)], { type: 'application/json' })) } catch { /* best-effort */ }
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
    title, vaultReady, workId, saveState, savedAt,
    changeKey, markDirty, flush, flushIfDirty, manualSave, onBeforeUnload, refreshInterval, stopAutosave, resetBaseline,
  }
}
