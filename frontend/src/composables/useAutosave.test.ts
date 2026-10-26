import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ref } from 'vue'

const saveWork = vi.fn()
vi.mock('../api', () => ({
  saveWork: (...a: any[]) => saveWork(...a),
  getAppSettings: vi.fn().mockResolvedValue({ autosave_interval_s: 300 }),
  ApiError: class ApiError extends Error { constructor(public status: number, msg: string) { super(msg) } },
}))
vi.mock('./useToast', () => ({ useToast: () => ({ push: vi.fn() }) }))

import { useAutosave } from './useAutosave'
import { ApiError } from '../api' // resolves to the mock's ApiError — same class useAutosave checks against
import type { PersistedCombo } from '../types'

function setup(combos: PersistedCombo[] = [], title = 'x') {
  const nodes = ref<any[]>([])
  const a = useAutosave({
    nodes, viewport: ref({ x: 0, y: 0, zoom: 1 }),
    params: () => ({}) as any, drafts: () => [], favorites: () => [], combos: () => combos, onNoVault: vi.fn(),
  })
  a.vaultReady.value = true
  a.title.value = title // a title makes the work "meaningful" so it saves
  return { nodes, a }
}

const combo: PersistedCombo = {
  id: 'combo-1', name: 'Raiden', origin: 'user',
  blocks: [{ source: 'custom', block_id: null, version: null, name: 'R', text: 'raiden shogun', polarity: 'positive', category: 'character', tags: [] }],
}

beforeEach(() => {
  saveWork.mockReset()
  saveWork.mockResolvedValue({ id: 'w', updated_at: '' })
})

describe('useAutosave save coordination', () => {
  it('saves a dirty work and resolves true', async () => {
    const { a } = setup()
    a.markDirty()
    expect(await a.flush()).toBe(true)
    expect(saveWork).toHaveBeenCalledTimes(1)
  })

  it('no-ops (true) when there is nothing to save', async () => {
    const { a } = setup()
    expect(await a.flush()).toBe(true) // not dirty, not forced
    expect(saveWork).not.toHaveBeenCalled()
  })

  it('coalesces an edit that lands mid-save into a second save (C1)', async () => {
    const { a, nodes } = setup()
    let release!: () => void
    saveWork.mockImplementationOnce(() => new Promise((r) => { release = () => r({}) })) // first save hangs
    a.markDirty()
    const p = a.flush()
    await Promise.resolve() // let the first saveWork fire
    nodes.value = [{ id: 'img-1', type: 'image', parentNode: 'gallery', data: { url: 'x' }, position: { x: 0, y: 0 }, style: {} }]
    a.markDirty() // edit during the in-flight save
    release()
    expect(await p).toBe(true)
    expect(saveWork).toHaveBeenCalledTimes(2) // the mid-save edit was re-saved, not lost
  })

  it('resolves false when the save fails (M1: New work can then confirm before wiping)', async () => {
    const { a } = setup()
    saveWork.mockRejectedValueOnce(new Error('boom'))
    a.markDirty()
    expect(await a.flush()).toBe(false)
  })

  it('on a 409 (concurrent edit) calls onConflict and does not stay dirty (H3)', async () => {
    const onConflict = vi.fn()
    const a = useAutosave({
      nodes: ref<any[]>([]), viewport: ref({ x: 0, y: 0, zoom: 1 }),
      params: () => ({}) as any, drafts: () => [], favorites: () => [], combos: () => [], onNoVault: vi.fn(), onConflict,
    })
    a.vaultReady.value = true
    a.title.value = 'x'
    saveWork.mockRejectedValueOnce(new ApiError(409, 'changed elsewhere'))
    a.markDirty()
    expect(await a.flush()).toBe(false)
    expect(onConflict).toHaveBeenCalledTimes(1)
  })
})

describe('useAutosave — catching up on an agent’s edits', () => {
  function withAgent(changes: () => Promise<{ at: string; ops: any[] } | null>) {
    const onConflict = vi.fn()
    const agentChanges = vi.fn(changes)
    const a = useAutosave({
      nodes: ref<any[]>([]), viewport: ref({ x: 0, y: 0, zoom: 1 }),
      params: () => ({}) as any, drafts: () => [], favorites: () => [], combos: () => [], onNoVault: vi.fn(), onConflict,
      agentChanges,
      applyAgentOps: (ops) => { for (const o of ops) if (o.op === 'title') a.title.value = o.title },
    })
    a.vaultReady.value = true
    a.title.value = 'x'
    a.baseUpdatedAt.value = 'T1'
    return { a, onConflict, agentChanges }
  }

  it('a 409 from an agent edit replays it and saves again on the new base — no reload', async () => {
    const { a, onConflict, agentChanges } = withAgent(async () => ({ at: 'T2', ops: [{ op: 'title', title: 'By the agent' }] }))
    saveWork.mockRejectedValueOnce(new ApiError(409, 'changed elsewhere')).mockResolvedValueOnce({ updated_at: 'T3' })
    a.markDirty()
    expect(await a.flush()).toBe(true)
    expect(agentChanges).toHaveBeenCalledWith(a.workId.value, 'T1')
    expect(onConflict).not.toHaveBeenCalled()
    const retry = saveWork.mock.calls[1][0]
    expect([retry.updated_at, retry.title]).toEqual(['T2', 'By the agent'])
    expect(a.baseUpdatedAt.value).toBe('T3')
  })

  it('a busy agent landing another edit before the re-save is caught up on too', async () => {
    const answers = [{ at: 'T2', ops: [{ op: 'title', title: 'First' }] }, { at: 'T3', ops: [{ op: 'title', title: 'Second' }] }]
    const { a, onConflict } = withAgent(async () => answers.shift() ?? null)
    saveWork.mockRejectedValueOnce(new ApiError(409, 'stale')).mockRejectedValueOnce(new ApiError(409, 'stale'))
      .mockResolvedValueOnce({ updated_at: 'T4' })
    a.markDirty()
    expect(await a.flush()).toBe(true)
    expect(onConflict).not.toHaveBeenCalled()
    expect(saveWork.mock.calls[2][0]).toMatchObject({ updated_at: 'T3', title: 'Second' })
  })

  it('a 409 with no agent edit to replay (e.g. a vault move) is not retried in a loop', async () => {
    const { a, onConflict } = withAgent(async () => ({ at: 'T1', ops: [] }))
    saveWork.mockRejectedValueOnce(new ApiError(409, 'A vault move is in progress'))
    a.markDirty()
    expect(await a.flush()).toBe(false)
    expect(saveWork).toHaveBeenCalledTimes(1)
    expect(onConflict).toHaveBeenCalledTimes(1)
  })

  it('a 409 from anyone else still reloads', async () => {
    const { a, onConflict } = withAgent(async () => null)
    saveWork.mockRejectedValueOnce(new ApiError(409, 'changed elsewhere'))
    a.markDirty()
    expect(await a.flush()).toBe(false)
    expect(onConflict).toHaveBeenCalledTimes(1)
    expect(saveWork).toHaveBeenCalledTimes(1)
  })

  it('a notice on a work with nothing unsaved adopts the edit without writing it back', async () => {
    const { a } = withAgent(async () => ({ at: 'T2', ops: [{ op: 'title', title: 'By the agent' }] }))
    await a.flush(true) // saved: nothing of ours is pending
    a.baseUpdatedAt.value = 'T1'
    saveWork.mockClear()
    await a.onAgentEdit()
    expect([a.title.value, a.baseUpdatedAt.value]).toEqual(['By the agent', 'T2'])
    expect(await a.flush(true)).toBe(true)
    expect(saveWork).not.toHaveBeenCalled() // the canvas IS the stored copy now
  })

  it('a notice during a save waits for it, then asks from the stamp that save got', async () => {
    const { a, agentChanges } = withAgent(async () => ({ at: 'T3', ops: [] }))
    let release!: () => void
    saveWork.mockImplementationOnce(() => new Promise((r) => { release = () => r({ updated_at: 'T2' }) }))
    a.markDirty()
    const saving = a.flush()
    await Promise.resolve()
    const caughtUp = a.onAgentEdit()
    release()
    await saving
    await caughtUp
    expect(agentChanges).toHaveBeenCalledWith(a.workId.value, 'T2')
    expect(a.baseUpdatedAt.value).toBe('T3')
  })
})

describe('useAutosave — combinations', () => {
  it('a work holding only combinations is worth saving', async () => {
    const { a } = setup([combo], '')
    a.markDirty()
    expect(await a.flush()).toBe(true)
    expect(saveWork).toHaveBeenCalledTimes(1)
  })

  it('both save paths write the combinations — the close beacon too', async () => {
    const { a } = setup([combo])
    a.markDirty()
    await a.flush()
    expect(saveWork.mock.calls[0][0].combos).toEqual([combo])

    const sendBeacon = vi.fn().mockReturnValue(true)
    vi.stubGlobal('navigator', { sendBeacon })
    try {
      a.title.value = 'y' // a fresh edit since the last save
      a.markDirty()
      a.onBeforeUnload()
      const body = JSON.parse(await (sendBeacon.mock.calls[0][1] as Blob).text())
      expect(body.combos).toEqual([combo])
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

describe('useAutosave — the cover', () => {
  it('a chosen cover is a change that saves, as the work’s preview', async () => {
    const { nodes, a } = setup()
    nodes.value = ['img-1', 'img-2'].map((id) => ({ id, type: 'image', parentNode: 'gallery', position: { x: 0, y: 0 }, style: {}, data: { url: 'u' } }))
    a.resetBaseline('saved')
    const before = a.changeKey()
    a.cover.value = 'img-2'
    expect(a.changeKey()).not.toBe(before)
    a.markDirty()
    await a.flush()
    expect(saveWork.mock.calls[0][0].preview_image_id).toBe('img-2')
  })
})

describe('useAutosave.changeKey — image domain fields (H1)', () => {
  function withImage(data: any) {
    const { nodes, a } = setup()
    nodes.value = [{ id: 'img-1', type: 'image', parentNode: 'gallery', position: { x: 0, y: 0 }, style: {}, data }]
    return { nodes, a }
  }
  it('reacts to a favourite toggle', () => {
    const { nodes, a } = withImage({ url: 'u', favorite: false })
    const before = a.changeKey()
    nodes.value[0].data.favorite = true
    expect(a.changeKey()).not.toBe(before)
  })
  it('reacts to a tag/description edit', () => {
    const { nodes, a } = withImage({ url: 'u', tags: [], description: '' })
    const before = a.changeKey()
    nodes.value[0].data.tags = ['portrait']; nodes.value[0].data.description = 'note'
    expect(a.changeKey()).not.toBe(before)
  })
  it('still ignores the image url value — bytes stay out of the signature', () => {
    const { nodes, a } = withImage({ url: 'data:image/png;base64,AAAA', favorite: false })
    const before = a.changeKey()
    nodes.value[0].data.url = '/api/vault/works/w1/images/img-1'
    expect(a.changeKey()).toBe(before)
  })
})
