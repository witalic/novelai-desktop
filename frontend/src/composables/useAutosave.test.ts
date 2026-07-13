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

function setup() {
  const nodes = ref<any[]>([])
  const a = useAutosave({
    nodes, viewport: ref({ x: 0, y: 0, zoom: 1 }),
    params: () => ({}) as any, drafts: () => [], favorites: () => [], onNoVault: vi.fn(),
  })
  a.vaultReady.value = true
  a.title.value = 'x' // makes the work "meaningful" so it saves
  return { nodes, a }
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
      params: () => ({}) as any, drafts: () => [], favorites: () => [], onNoVault: vi.fn(), onConflict,
    })
    a.vaultReady.value = true
    a.title.value = 'x'
    saveWork.mockRejectedValueOnce(new ApiError(409, 'changed elsewhere'))
    a.markDirty()
    expect(await a.flush()).toBe(false)
    expect(onConflict).toHaveBeenCalledTimes(1)
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
