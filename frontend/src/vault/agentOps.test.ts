import { describe, expect, it, vi } from 'vitest'
import { applyAgentOpsToDoc, applyGalleryOps, catchUpQueue, planAgentOps, type AgentOp, type GalleryOp } from './agentOps'
import { gridsOf } from './workOps'
import type { GalleryBlock, PersistedCombo, WorkDoc } from '../types'

const combo = (id: string, name: string): PersistedCombo => ({
  id, name, origin: 'user',
  blocks: [{ source: 'custom', block_id: null, version: null, name: '', text: name.toLowerCase(), polarity: 'positive', category: 'custom', tags: [] }],
})

describe('planAgentOps', () => {
  const mine = [combo('c-1', 'Mine'), combo('c-2', 'Other')]

  it('sets what the agent set and leaves the rest of the copy alone', () => {
    const ops: AgentOp[] = [
      { op: 'title', title: 'Renamed' },
      { op: 'combo', combo: { ...combo('c-3', 'New'), origin: 'agent' } },
      { op: 'combo', combo: combo('c-2', 'Other, edited') },
      { op: 'image', id: 'img-1', set: { tags: ['night'] } },
      { op: 'image', id: 'img-1', set: { favorite: true } },
    ]
    const edits = planAgentOps(ops, mine)
    expect(edits.title).toBe('Renamed')
    expect(edits.combos.map((c) => c.name)).toEqual(['New', 'Mine', 'Other, edited']) // new on top, edited in place
    expect(edits.images.get('img-1')).toEqual({ tags: ['night'], favorite: true })
    expect(mine[1].name).toBe('Other') // the copy passed in is never mutated
  })

  it('sets the station settings the agent set — the latest of each, and the preset it named last', () => {
    const edits = planAgentOps([
      { op: 'params', set: { steps: 23, uc_preset: 3 }, preset_id: 'preset-a' },
      { op: 'params', set: { steps: 24 } },
    ], mine)
    expect(edits.params).toEqual({ steps: 24, uc_preset: 3 })
    expect(edits.presetId).toBe('preset-a')
    expect(planAgentOps([{ op: 'title', title: 'x' }], mine).params).toBeUndefined() // nothing to set: the station stays
  })

  it('removes what the agent removed', () => {
    const edits = planAgentOps([
      { op: 'image', id: 'img-1', set: { favorite: true } },
      { op: 'images_deleted', ids: ['img-1', 'img-2'] },
      { op: 'combo_deleted', id: 'c-1' },
    ], mine)
    expect(edits.deleted).toEqual(['img-1', 'img-2'])
    expect(edits.images.has('img-1')).toBe(false)
    expect(edits.combos.map((c) => c.id)).toEqual(['c-2'])
    expect(edits.title).toBeUndefined()
  })

  it('the agent wins per item: its version replaces an unsaved local edit, and an edited one comes back', () => {
    const local = [{ ...combo('c-1', 'Mine'), name: 'Mine, renamed locally' }] // c-2 deleted locally, unsaved
    const edits = planAgentOps([{ op: 'combo', combo: combo('c-1', 'Mine') }, { op: 'combo', combo: combo('c-2', 'Other') }], local)
    expect(edits.combos.map((c) => c.name)).toEqual(['Other', 'Mine'])
  })

  it('replaying the same edits twice changes nothing more', () => {
    const ops: AgentOp[] = [{ op: 'combo', combo: combo('c-3', 'New') }, { op: 'combo_deleted', id: 'c-1' }]
    const once = planAgentOps(ops, mine).combos
    expect(planAgentOps(ops, once).combos).toEqual(once)
  })
})

describe('catchUpQueue', () => {
  it('a call during a run shares it and makes it run once more', async () => {
    const releases: (() => void)[] = []
    const run = vi.fn(() => new Promise<boolean>((r) => releases.push(() => r(releases.length === 1))))
    const catchUp = catchUpQueue(run)
    const first = catchUp()
    const second = catchUp() // an agent edit noticed while the first run is asking
    expect(second).toBe(first)
    releases[0]() // the first run moved the copy on…
    await Promise.resolve(); await Promise.resolve()
    expect(run).toHaveBeenCalledTimes(2) // …and a second run picks up what landed meanwhile
    releases[1]()
    expect(await first).toBe(true)
    expect(run).toHaveBeenCalledTimes(2)
  })
})

describe('applyAgentOpsToDoc', () => {
  it('edits a whole work in place — images, albums, combinations and title', () => {
    const doc = {
      schema_version: 7, id: 'w', title: 'T', params: {}, snapshots: [], stack: [], favorites: [],
      combos: [combo('c-1', 'Mine')], preview_image_id: 'img-1',
      images: [
        { id: 'img-1', role: 'gallery', tags: [], description: '', favorite: false },
        { id: 'img-2', role: 'gallery', tags: [], description: '', favorite: false },
      ],
      canvas: { nodes: [
        { id: 'gallery', type: 'zone', data: { role: 'gallery', blocks: [{ id: 'g', type: 'grid', imageIds: ['img-1', 'img-2'] }] } },
        { id: 'img-1', type: 'image' }, { id: 'img-2', type: 'image' },
      ] },
    } as unknown as WorkDoc
    doc.params = { steps: 20, seed: 77 }
    applyAgentOpsToDoc(doc, [
      { op: 'title', title: 'Renamed' },
      { op: 'params', set: { steps: 24, n_samples: 2 } },
      { op: 'image', id: 'img-2', set: { description: 'moonlit' } },
      { op: 'images_deleted', ids: ['img-1'] },
      { op: 'combo_deleted', id: 'c-1' },
    ])
    expect(doc.title).toBe('Renamed')
    expect(doc.params).toEqual({ steps: 24, seed: 77, n_samples: 2 }) // what the agent set; the seed stays
    expect(doc.combos).toEqual([])
    expect(doc.images.map((im) => [im.id, im.description])).toEqual([['img-2', 'moonlit']])
    expect(doc.preview_image_id).toBe('img-2')
    expect(((doc.canvas.nodes[0].data as any).blocks[0]).imageIds).toEqual(['img-2'])
  })

  it('adds generated images with their recipes — once, however often replayed', () => {
    const doc = { id: 'w', title: 'T', combos: [], images: [], snapshots: [], preview_image_id: null, canvas: { nodes: [] } } as unknown as WorkDoc
    const ops: AgentOp[] = [{
      op: 'images_added',
      images: [{ id: 'img-g', snapshot_id: 'snap-g', role: 'gallery', ar: 1, file: 'images/img-g.png', created_at: '', group: null, favorite: false, tags: [], description: '' }],
      snapshots: [{ id: 'snap-g', hash: 'positive:moon', components: [], assembled_positive: 'moon', assembled_negative: '', params: { seed: 3 } }],
    }]
    applyAgentOpsToDoc(doc, ops)
    applyAgentOpsToDoc(doc, ops)
    expect(doc.images.map((im) => im.id)).toEqual(['img-g'])
    expect(doc.snapshots.map((s) => s.id)).toEqual(['snap-g'])
    expect(doc.preview_image_id).toBe('img-g')
    expect(planAgentOps([...ops, { op: 'images_deleted', ids: ['img-g'] }], []).added).toEqual([]) // made, then deleted
  })

  it('gives a work without a gallery zone one, arranges it and sets the cover', () => {
    const doc = {
      id: 'w', title: 'T', combos: [], snapshots: [], preview_image_id: 'a', canvas: { nodes: [] },
      images: [{ id: 'a', role: 'gallery' }, { id: 'b', role: 'gallery' }, { id: 's', role: 'scratch' }],
    } as unknown as WorkDoc
    const ops: AgentOp[] = [
      { op: 'gallery_add', block: { id: 'g', type: 'grid', imageIds: [], cols: 3 }, before: null },
      { op: 'gallery_place', grid: 'g', image_ids: ['b', 's'], order: ['b', 's'] }, // a scratch image never joins
      { op: 'cover', image_id: 'b' },
    ]
    applyAgentOpsToDoc(doc, ops)
    applyAgentOpsToDoc(doc, ops)
    expect(doc.canvas.nodes).toEqual([
      { id: 'gallery', type: 'zone', data: { role: 'gallery', blocks: [{ id: 'g', type: 'grid', imageIds: ['b'], cols: 3 }] } }])
    expect(doc.preview_image_id).toBe('b')
    applyAgentOpsToDoc(doc, [{ op: 'cover', image_id: 'a' }, { op: 'images_deleted', ids: ['a'] }])
    expect(doc.preview_image_id).toBe('b') // a cover deleted in the same catch-up moves on, as on the backend
  })
})

describe('applyGalleryOps', () => {
  const stack = (): GalleryBlock[] => [
    { id: 'h-1', type: 'heading', text: 'Intro', level: 1 },
    { id: 's-1', type: 'section', title: 'Picks', collapsed: false },
    { id: 'g-1', type: 'grid', imageIds: ['a', 'b'], cols: 4 },
    { id: 's-2', type: 'section', title: 'Rest', collapsed: true },
    { id: 'd-1', type: 'divider' },
  ]
  const inGallery = (id: string) => ['a', 'b', 'c', 'd'].includes(id)
  // An arrangement as the backend logs it (app/agent/gallery.py — same operations, same rules).
  const ops: GalleryOp[] = [
    { op: 'gallery_add', block: { id: 'night', type: 'section', title: 'Night', collapsed: false }, before: 's-2' },
    { op: 'gallery_add', block: { id: 'album', type: 'grid', imageIds: [], cols: 3 }, before: 's-2' },
    { op: 'gallery_place', grid: 'album', image_ids: ['d', 'c'], order: ['d', 'c'] },
    { op: 'gallery_place', grid: 'album', image_ids: ['b'], order: ['d', 'b', 'c'] }, // out of g-1, between d and c
    { op: 'gallery_set', id: 'g-1', set: { cols: 2, collapsed: true } },
    { op: 'gallery_order', order: ['h-1', 's-2', 'd-1', 's-1', 'g-1', 'night', 'album'] }, // Rest and its divider up
    { op: 'gallery_remove', id: 'h-1' },
  ]

  it('lands an agent’s arrangement — once, however often replayed', () => {
    const blocks = stack()
    applyGalleryOps(blocks, ops, inGallery)
    applyGalleryOps(blocks, ops, inGallery)
    expect(blocks.map((b) => b.id)).toEqual(['s-2', 'd-1', 's-1', 'g-1', 'night', 'album'])
    expect(gridsOf(blocks).map((g) => [g.id, g.imageIds])).toEqual([['g-1', ['a']], ['album', ['d', 'b', 'c']]])
    expect(blocks[3]).toMatchObject({ cols: 2, collapsed: true })
    expect(ops[1]).toMatchObject({ block: { imageIds: [] } }) // the operation itself is never changed
  })

  it('keeps the user’s unsaved layout edits around the agent’s', () => {
    const blocks = stack()
    blocks.splice(0, 1, { id: 'mine', type: 'text', text: 'my note' }) // the heading replaced meanwhile
    blocks.splice(3, 2) // and Rest deleted, with its divider
    applyGalleryOps(blocks, ops, (id) => id !== 'd') // and image d moved out onto the canvas
    expect(blocks.map((b) => b.id)).toEqual(['mine', 's-1', 'g-1', 'night', 'album'])
    expect(gridsOf(blocks).map((g) => [g.id, g.imageIds])).toEqual([['g-1', ['a']], ['album', ['b', 'c']]])
  })

  it('a batch whose later op moves an earlier one’s neighbour still replays to the same order', () => {
    const grid = (ids: string[]): GalleryBlock[] => [{ id: 'g', type: 'grid', imageIds: ids, cols: 3 }]
    const batch: GalleryOp[] = [
      { op: 'gallery_place', grid: 'g', image_ids: ['a'], order: ['a', 'b', 'c'] }, // a first, next to b…
      { op: 'gallery_place', grid: 'g', image_ids: ['b'], order: ['a', 'c', 'b'] }, // …then b to the end
    ]
    const blocks = grid(['b', 'c'])
    for (let n = 0; n < 3; n++) {
      applyGalleryOps(blocks, batch, inGallery)
      expect(gridsOf(blocks)[0].imageIds).toEqual(['a', 'c', 'b'])
    }
    const mine = grid(['b', 'x', 'c']) // x: the user's own, unsaved — it keeps its slot
    applyGalleryOps(mine, batch, (id) => id !== 'zz')
    expect(gridsOf(mine)[0].imageIds).toEqual(['a', 'x', 'c', 'b'])
  })

  it('skips what is gone and clears a field set to null', () => {
    const blocks: GalleryBlock[] = [...stack(), { id: 'm-1', type: 'meta', fields: [], gridId: 'g-1' }]
    applyGalleryOps(blocks, [
      { op: 'gallery_place', grid: 'gone', image_ids: ['a'], order: ['a'] }, // a deleted grid: a stays where it is
      { op: 'gallery_set', id: 'gone', set: { title: 'x' } },
      { op: 'gallery_unplace', image_ids: ['b'] },
      { op: 'gallery_set', id: 'm-1', set: { gridId: null } }, // summarise the whole gallery again
    ], inGallery)
    expect(gridsOf(blocks)[0].imageIds).toEqual(['a'])
    expect(blocks.find((b) => b.id === 'm-1')).toEqual({ id: 'm-1', type: 'meta', fields: [] })
  })
})
