/* Saved block combinations (framework-free, unit-tested): build one from the station composition or an
 * image's recipe, compare it with the composition (the station header's saved/edited state), plan an Add
 * or a Replace, and track Library drift. A combination stores blocks only — never generation params. */
import type { LibraryBlock, PersistedCombo, PersistedComboBlock, PersistedComponent } from '../types'
import { newId } from './ids'

// What these helpers read off a station composition row (a block node's data), in station order.
export interface CompBlockData {
  category?: string
  name?: string
  text?: string
  polarity?: string
  block_id?: string | null
  version?: number | null
  tags?: string[]
  frozen?: boolean
}

const pol = (p: unknown): 'positive' | 'negative' => (p === 'negative' ? 'negative' : 'positive')

// A block's identity in the prompt — polarity + text (trimmed, case-insensitive). The one rule for "already in
// the composition": the station refuses duplicates by it, the Library widget greys used blocks, Add skips by it.
export const blockIdentity = (b: { polarity?: unknown; text?: unknown }): string =>
  `${pol(b.polarity) === 'negative' ? 'neg' : 'pos'}:${String(b.text || '').trim().toLowerCase()}`

function toComboBlock(d: CompBlockData | PersistedComponent, frozen: boolean): PersistedComboBlock {
  return {
    source: d.block_id ? 'library' : 'custom', block_id: d.block_id ?? null, version: d.version ?? null,
    name: d.name || '', text: String(d.text || '').trim(), polarity: pol(d.polarity),
    category: d.category || 'custom', tags: [...(d.tags || [])],
    ...(frozen ? { frozen: true } : {}),
  }
}

// The station's blocks (station order) as combination blocks. `keepFrozen` off leaves frozen rows out.
export function comboBlocksFromComposition(rows: CompBlockData[], keepFrozen = true): PersistedComboBlock[] {
  return rows.filter((d) => keepFrozen || !d.frozen).map((d) => toComboBlock(d, !!d.frozen))
}

// An image's recipe (its snapshot components — frozen blocks were never part of it) as combination blocks.
export function comboBlocksFromComponents(components: PersistedComponent[]): PersistedComboBlock[] {
  return components.map((c) => toComboBlock(c, false))
}

// An independent plain copy (the loaded doc arrives as a Vue reactive proxy, which structuredClone rejects).
export function cloneCombos(combos: PersistedCombo[]): PersistedCombo[] {
  return combos.map((c) => ({ ...c, blocks: c.blocks.map((b) => ({ ...b, tags: [...(b.tags || [])] })) }))
}

// Stamped here (the save response carries no per-combination times); the server only fills times a writer left out.
export function newCombo(name: string, blocks: PersistedComboBlock[], origin: 'user' | 'agent' = 'user'): PersistedCombo {
  const now = new Date().toISOString()
  return { id: newId('combo'), name: name.trim() || 'Untitled combination', blocks, origin, created_at: now, updated_at: now }
}

export function duplicateCombo(c: PersistedCombo): PersistedCombo {
  return newCombo(`${c.name} (copy)`, cloneCombos([c])[0].blocks)
}

// Station node data for a combination block (the composition row it restores into).
export function compDataFromComboBlock(b: PersistedComboBlock): CompBlockData & { expanded: false } {
  return {
    category: b.category || 'custom', name: b.name, text: b.text, polarity: pol(b.polarity),
    ...(b.block_id ? { block_id: b.block_id, version: b.version ?? 1 } : {}),
    tags: [...(b.tags || [])], ...(b.frozen ? { frozen: true } : {}), expanded: false,
  }
}

// Does the station hold exactly this combination — same blocks, same order, same names/categories/frozen?
// (False → the header shows "edited".)
export function matchesComposition(combo: PersistedCombo, rows: CompBlockData[]): boolean {
  const sig = (b: CompBlockData | PersistedComboBlock) =>
    [pol(b.polarity), String(b.text || '').trim(), b.name || '', b.category || 'custom', !!b.frozen].join('\u0001')
  return combo.blocks.length === rows.length && combo.blocks.every((b, i) => sig(b) === sig(rows[i]))
}

// Add = append the combination's blocks after the station's; ones already there (same identity) are skipped.
export function planAdd(blocks: PersistedComboBlock[], rows: CompBlockData[]): { toAdd: PersistedComboBlock[]; skipped: number } {
  const have = new Set(rows.map(blockIdentity))
  const toAdd: PersistedComboBlock[] = []
  let skipped = 0
  for (const b of blocks) {
    const key = blockIdentity(b)
    if (have.has(key)) { skipped += 1; continue }
    have.add(key)
    toAdd.push(b)
  }
  return { toAdd, skipped }
}

// Replace drops every station block that isn't in the combination — how many would be lost (0 → no question).
export function lostOnReplace(rows: CompBlockData[], blocks: PersistedComboBlock[]): number {
  const keep = new Set(blocks.map(blockIdentity))
  return rows.filter((r) => !keep.has(blockIdentity(r))).length
}

// Library blocks edited after the combination saved them (a newer version) — the card's drift badge. An
// unversioned ref counts as v1, as everywhere else (compDataFromComboBlock, the Library's first version).
export function libraryDrift(combo: PersistedCombo, latestVersions: Map<string, number>): number {
  return combo.blocks.filter((b) => {
    if (!b.block_id) return false
    const v = latestVersions.get(b.block_id)
    return v !== undefined && v > (b.version ?? 1)
  }).length
}

// "Use latest Library versions": refresh each Library-linked block's content from the vault (text, name, …),
// keeping what the composition decided — its polarity and frozen state. Blocks whose Library block is gone
// keep their saved copy.
export function withLatestLibrary(blocks: PersistedComboBlock[], latest: Map<string, LibraryBlock>): PersistedComboBlock[] {
  return blocks.map((b) => {
    const lib = b.block_id ? latest.get(b.block_id) : undefined
    if (!lib) return b
    return {
      ...b, name: lib.name, text: lib.text, category: lib.category,
      tags: [...(lib.tags || [])], version: lib.version ?? b.version ?? null,
    }
  })
}
