import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api'
import { usePromptBrowse, type BrowseDeps } from './usePromptBrowse'
import type { BlocksPage, LibraryBlock } from '../types'

/* The widget's browse data layer: race guard, search debounce, page-append, no-vault state. */

function block(id: string): LibraryBlock {
  return { id, category: 'custom', name: id, text: id, polarity: 'positive', tags: [] }
}
function pageOf(ids: string[], total = ids.length): BlocksPage {
  return { items: ids.map(block), total, page: 1, per_page: 48 }
}
function deps(over: Partial<BrowseDeps> = {}): BrowseDeps {
  return {
    listBlocks: vi.fn(async () => pageOf(['a', 'b'])),
    listCategories: vi.fn(async () => []),
    listTags: vi.fn(async () => []),
    ...over,
  }
}
const tick = () => new Promise<void>((r) => { setTimeout(r, 0); vi.advanceTimersByTime(0) })

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('usePromptBrowse', () => {
  it('activate() loads filters and the first page', async () => {
    const d = deps()
    const b = usePromptBrowse(d)
    b.activate()
    await tick(); await tick()
    expect(b.items.value.map((i) => i.id)).toEqual(['a', 'b'])
    expect(d.listCategories).toHaveBeenCalled()
    expect(d.listTags).toHaveBeenCalled()
  })

  it('drops a slow earlier response that resolves after a newer request', async () => {
    let resolveFirst!: (v: BlocksPage) => void
    const first = new Promise<BlocksPage>((r) => { resolveFirst = r })
    const listBlocks = vi.fn()
      .mockReturnValueOnce(first)                       // slow request
      .mockResolvedValueOnce(pageOf(['new']))           // fast newer request
    const b = usePromptBrowse(deps({ listBlocks }))
    b.toggleCategory('style')   // request 1 (hangs)
    b.toggleCategory('style')   // toggles back off → request 2 (fast)
    await tick()
    resolveFirst(pageOf(['stale']))                     // the slow one lands last…
    await tick()
    expect(b.items.value.map((i) => i.id)).toEqual(['new']) // …and is discarded
  })

  it('multi-selects categories and passes the sort through', async () => {
    const listBlocks = vi.fn(async () => pageOf(['x']))
    const b = usePromptBrowse(deps({ listBlocks }))
    b.toggleCategory('style')
    b.toggleCategory('pose')
    await tick()
    expect(listBlocks).toHaveBeenLastCalledWith(expect.objectContaining({ categories: ['style', 'pose'] }))
    b.setSort('category')
    await tick()
    expect(listBlocks).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'category' }))
    b.toggleCategory('') // 'All' resets
    await tick()
    expect(listBlocks).toHaveBeenLastCalledWith(expect.objectContaining({ categories: [] }))
  })

  it('debounces search input into one request', async () => {
    const listBlocks = vi.fn(async () => pageOf(['x']))
    const b = usePromptBrowse(deps({ listBlocks }))
    b.setSearch('s')
    b.setSearch('si')
    b.setSearch('sil')
    expect(listBlocks).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(300)
    expect(listBlocks).toHaveBeenCalledTimes(1)
    expect(listBlocks).toHaveBeenCalledWith(expect.objectContaining({ search: 'sil' }))
  })

  it('loadMore appends the next page and stops at total', async () => {
    const listBlocks = vi.fn()
      .mockResolvedValueOnce(pageOf(['a', 'b'], 3))
      .mockResolvedValueOnce(pageOf(['c'], 3))
    const b = usePromptBrowse(deps({ listBlocks }))
    b.activate()
    await tick(); await tick()
    b.loadMore()
    await tick()
    expect(b.items.value.map((i) => i.id)).toEqual(['a', 'b', 'c'])
    expect(listBlocks).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))
    b.loadMore() // everything is loaded → no further request
    expect(listBlocks).toHaveBeenCalledTimes(2)
  })

  it('flags no-vault on 409 instead of erroring', async () => {
    const listBlocks = vi.fn(async () => { throw new ApiError(409, 'no vault') })
    const b = usePromptBrowse(deps({ listBlocks }))
    b.activate()
    await tick(); await tick()
    expect(b.noVault.value).toBe(true)
    expect(b.items.value).toEqual([])
  })
})
