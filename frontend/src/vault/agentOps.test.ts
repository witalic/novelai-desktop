import { describe, expect, it, vi } from 'vitest'
import { applyAgentOpsToDoc, catchUpQueue, planAgentOps, type AgentOp } from './agentOps'
import type { PersistedCombo, WorkDoc } from '../types'

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
    applyAgentOpsToDoc(doc, [
      { op: 'title', title: 'Renamed' },
      { op: 'image', id: 'img-2', set: { description: 'moonlit' } },
      { op: 'images_deleted', ids: ['img-1'] },
      { op: 'combo_deleted', id: 'c-1' },
    ])
    expect(doc.title).toBe('Renamed')
    expect(doc.combos).toEqual([])
    expect(doc.images.map((im) => [im.id, im.description])).toEqual([['img-2', 'moonlit']])
    expect(doc.preview_image_id).toBe('img-2')
    expect(((doc.canvas.nodes[0].data as any).blocks[0]).imageIds).toEqual(['img-2'])
  })
})
