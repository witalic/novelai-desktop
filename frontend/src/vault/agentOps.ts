/* Replaying an agent's edits of a work (backend app/agent/changes) over the app's open copy of it — which may hold
 * unsaved edits of its own. Each operation sets what the agent set (the title, a combination, an image's metadata)
 * or removes what it removed; everything else in the copy stays as the user left it. Replaying an edit twice
 * changes nothing, so overlapping catch-ups are harmless. Pure → tested. */
import type { PersistedCombo, WorkDoc } from '../types'
import { cloneCombos } from './combos'
import { purgeImages } from './workOps'

export interface ImageFields { tags?: string[]; description?: string; favorite?: boolean }

export type AgentOp =
  | { op: 'title'; title: string }
  | { op: 'combo'; combo: PersistedCombo } // created or edited — the whole combination
  | { op: 'combo_deleted'; id: string }
  | { op: 'image'; id: string; set: ImageFields }
  | { op: 'images_deleted'; ids: string[] }

export interface AgentEdits {
  title?: string
  combos: PersistedCombo[] // the copy's combinations with the agent's applied
  images: Map<string, ImageFields> // per image id, the metadata to set
  deleted: string[] // image ids to remove
}

export function planAgentOps(ops: AgentOp[], combos: PersistedCombo[]): AgentEdits {
  let next = cloneCombos(combos)
  const images = new Map<string, ImageFields>()
  const deleted = new Set<string>()
  let title: string | undefined
  for (const o of ops) {
    if (o.op === 'title') title = o.title
    else if (o.op === 'combo') {
      const c = cloneCombos([o.combo])[0]
      const at = next.findIndex((x) => x.id === c.id)
      if (at >= 0) next[at] = c
      else next.unshift(c) // a new one goes on top, as the widget lists them (newest first)
    } else if (o.op === 'combo_deleted') next = next.filter((c) => c.id !== o.id)
    else if (o.op === 'image') images.set(o.id, { ...images.get(o.id), ...o.set })
    else if (o.op === 'images_deleted') for (const id of o.ids) { deleted.add(id); images.delete(id) }
  }
  return { title, combos: next, images, deleted: [...deleted] }
}

/** Catching up, one run at a time: a call during a run (another agent edit, a save's 409) makes it run once more
 *  — that run may have asked before the newest edit landed — and shares its promise. Resolves true when any run
 *  moved the copy on (`run` says so). */
export function catchUpQueue(run: () => Promise<boolean>): () => Promise<boolean> {
  let running: Promise<boolean> | null = null
  let again = false
  return () => {
    if (running) { again = true; return running }
    running = (async () => {
      let moved = false
      do {
        again = false
        if (await run()) moved = true
      } while (again)
      return moved
    })().finally(() => { running = null })
    return running
  }
}

/** The same edits on a whole WorkDoc, in place (the Works editor's copy). */
export function applyAgentOpsToDoc(doc: WorkDoc, ops: AgentOp[]): void {
  const edits = planAgentOps(ops, doc.combos || [])
  if (edits.title !== undefined) doc.title = edits.title
  doc.combos = edits.combos
  for (const im of doc.images) {
    const set = edits.images.get(im.id)
    if (set) Object.assign(im, set)
  }
  if (edits.deleted.length) purgeImages(doc, edits.deleted)
}
