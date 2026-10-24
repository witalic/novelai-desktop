import { describe, expect, it } from 'vitest'
import { reactive } from 'vue'
import {
  blockIdentity, cloneCombos, comboBlocksFromComponents, comboBlocksFromComposition, compDataFromComboBlock, duplicateCombo,
  libraryDrift, lostOnReplace, matchesComposition, newCombo, planAdd, withLatestLibrary,
} from './combos'
import type { LibraryBlock } from '../types'

const rows = [
  { category: 'style', name: 'Nixeu', text: ' artist:nixeu ', polarity: 'positive', block_id: 'b1', version: 2, tags: ['nixeu'] },
  { category: 'character', name: 'Raiden', text: 'raiden shogun', polarity: 'positive', block_id: 'b2', version: 1, tags: [] },
  { category: 'scene-camera', name: 'From side', text: 'from side', polarity: 'positive', frozen: true },
  { category: 'negative', name: 'Sig', text: 'signature', polarity: 'negative' },
]

describe('building combinations', () => {
  it('takes the station rows in order, with Library refs, trimmed text and frozen state', () => {
    const blocks = comboBlocksFromComposition(rows)
    expect(blocks.map((b) => b.text)).toEqual(['artist:nixeu', 'raiden shogun', 'from side', 'signature'])
    expect(blocks[0]).toMatchObject({ source: 'library', block_id: 'b1', version: 2, category: 'style' })
    expect(blocks[3]).toMatchObject({ source: 'custom', block_id: null, polarity: 'negative' })
    expect(blocks[2].frozen).toBe(true)
    expect('frozen' in blocks[0]).toBe(false) // only frozen rows carry the flag
  })

  it('leaves frozen rows out when asked not to keep them', () => {
    expect(comboBlocksFromComposition(rows, false).map((b) => b.name)).toEqual(['Nixeu', 'Raiden', 'Sig'])
  })

  it('builds from an image recipe (snapshot components)', () => {
    const blocks = comboBlocksFromComponents([{ source: 'library', block_id: 'b1', version: 3, name: 'N', text: 'x', polarity: 'positive', tags: [] }])
    expect(blocks).toEqual([{ source: 'library', block_id: 'b1', version: 3, name: 'N', text: 'x', polarity: 'positive', category: 'custom', tags: [] }])
  })

  it('names new and duplicated combinations, never empty', () => {
    expect(newCombo('  ', []).name).toBe('Untitled combination')
    const c = newCombo('Raiden', comboBlocksFromComposition(rows))
    const d = duplicateCombo(c)
    expect(d.name).toBe('Raiden (copy)')
    expect(d.id).not.toBe(c.id)
    d.blocks[0].tags.push('x')
    expect(c.blocks[0].tags).toEqual(['nixeu']) // a deep copy
  })

  it('stamps its own times — the save response never carries them back', () => {
    const c = newCombo('Raiden', [])
    expect(c.created_at).toBeTruthy()
    expect(c.updated_at).toBe(c.created_at)
  })

  it('copies a reactive (loaded-work) list into plain, independent objects', () => {
    // A loaded WorkDoc reaches the canvas as a Vue reactive proxy — structuredClone throws on it.
    const live = reactive([newCombo('Raiden', comboBlocksFromComposition(rows))])
    const copy = cloneCombos(live)
    expect(copy).toEqual(JSON.parse(JSON.stringify(live)))
    copy[0].blocks[0].tags.push('x')
    expect(live[0].blocks[0].tags).toEqual(['nixeu'])
  })

  it('turns a block back into station node data', () => {
    const [nixeu, , side] = comboBlocksFromComposition(rows)
    expect(compDataFromComboBlock(nixeu)).toEqual({ category: 'style', name: 'Nixeu', text: 'artist:nixeu', polarity: 'positive', block_id: 'b1', version: 2, tags: ['nixeu'], expanded: false })
    expect(compDataFromComboBlock(side)).toMatchObject({ frozen: true })
    expect('block_id' in compDataFromComboBlock(side)).toBe(false)
  })
})

describe('comparing with the station', () => {
  const combo = newCombo('Raiden', comboBlocksFromComposition(rows))

  it('matches the exact composition it was saved from', () => {
    expect(matchesComposition(combo, rows)).toBe(true)
  })

  it('reports an edit on reorder, rename, text, polarity, freeze, or a block added/removed', () => {
    expect(matchesComposition(combo, [rows[1], rows[0], rows[2], rows[3]])).toBe(false)
    expect(matchesComposition(combo, [{ ...rows[0], name: 'Other' }, ...rows.slice(1)])).toBe(false)
    expect(matchesComposition(combo, [{ ...rows[0], text: 'artist:wlop' }, ...rows.slice(1)])).toBe(false)
    expect(matchesComposition(combo, [...rows.slice(0, 3), { ...rows[3], polarity: 'positive' }])).toBe(false)
    expect(matchesComposition(combo, [rows[0], rows[1], { ...rows[2], frozen: false }, rows[3]])).toBe(false)
    expect(matchesComposition(combo, rows.slice(0, 3))).toBe(false)
  })
})

describe('applying', () => {
  const blocks = comboBlocksFromComposition(rows)

  it('Add skips blocks already on the station (same polarity + text, case-insensitive)', () => {
    const station = [{ text: 'ARTIST:NIXEU', polarity: 'positive' }, { text: 'signature', polarity: 'positive' }]
    const { toAdd, skipped } = planAdd(blocks, station)
    expect(skipped).toBe(1) // nixeu; the positive "signature" is a different block from the negative one
    expect(toAdd.map((b) => b.name)).toEqual(['Raiden', 'From side', 'Sig'])
  })

  it('Add never adds the same block twice from one combination', () => {
    const { toAdd, skipped } = planAdd([blocks[0], { ...blocks[0] }], [])
    expect(toAdd).toHaveLength(1)
    expect(skipped).toBe(1)
  })

  it('Replace counts only the station blocks the combination does not have', () => {
    expect(lostOnReplace(rows, blocks)).toBe(0)
    expect(lostOnReplace([...rows, { text: 'smile', polarity: 'positive' }], blocks)).toBe(1)
  })

  it('identity ignores surrounding whitespace and case', () => {
    expect(blockIdentity({ text: '  Smile ', polarity: 'positive' })).toBe(blockIdentity({ text: 'smile' }))
  })
})

describe('Library drift', () => {
  const combo = newCombo('Raiden', comboBlocksFromComposition(rows))

  it('counts Library blocks edited after the combination saved them', () => {
    expect(libraryDrift(combo, new Map([['b1', 2], ['b2', 1]]))).toBe(0)
    expect(libraryDrift(combo, new Map([['b1', 3], ['b2', 1]]))).toBe(1)
    expect(libraryDrift(combo, new Map())).toBe(0) // deleted/unknown Library blocks are not "changed"
  })

  it('reads an unversioned ref as v1, so it is not "changed" against a first-version block', () => {
    const old = { ...combo, blocks: [{ ...combo.blocks[0], version: null }] }
    expect(libraryDrift(old, new Map([['b1', 1]]))).toBe(0)
    expect(libraryDrift(old, new Map([['b1', 2]]))).toBe(1)
  })

  it('refreshes Library-linked blocks to their latest version, keeping frozen state, polarity and customs', () => {
    const frozenFlipped = { ...combo.blocks[0], frozen: true, polarity: 'negative' as const }
    const latest = new Map<string, LibraryBlock>([['b1', { id: 'b1', category: 'style', name: 'Nixeu v3', text: 'artist:nixeu, painterly', polarity: 'positive', tags: ['nixeu', 'v3'], version: 3 }]])
    const [a, b, c] = withLatestLibrary([frozenFlipped, combo.blocks[1], combo.blocks[3]], latest)
    expect(a).toMatchObject({ name: 'Nixeu v3', text: 'artist:nixeu, painterly', version: 3, frozen: true, polarity: 'negative', tags: ['nixeu', 'v3'] })
    expect(b).toEqual(combo.blocks[1]) // its Library block wasn't returned → saved copy kept
    expect(c).toEqual(combo.blocks[3]) // custom block untouched
  })
})
