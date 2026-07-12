import { describe, expect, it, vi } from 'vitest'
import { usePromptLibrary } from './usePromptLibrary'
import type { BlocksPage, CategoryCount, LibraryBlock, TagCount } from '../types'

/* usePromptLibrary is framework-light and dependency-injected — these pin the two data paths:
 * the backend browse (listBlocks with a single-select category) and the client-side favorites
 * filter (resolveBlocks + category/tag/search narrowing, with fav-derived rail counts). */

const BLOCKS: LibraryBlock[] = [
  { id: 'b1', category: 'body', name: 'Curvy', text: 'curvy', polarity: 'positive', tags: ['figure'] },
  { id: 'b2', category: 'body', name: 'Athletic', text: 'athletic', polarity: 'positive', tags: ['fit'] },
  { id: 'b3', category: 'scene-lighting', name: 'Golden hour', text: 'golden hour', polarity: 'positive', tags: ['warm'] },
]
const CATS: CategoryCount[] = [
  { slug: 'body', name: 'Body', color: '#c77d54', count: 2, builtin: true },
  { slug: 'scene-lighting', name: 'Scene · Lighting', color: '#b65c02', count: 1, builtin: true },
]

function deps(favorites: string[]) {
  const listBlocks = vi.fn(async (opts: { categories?: string[]; search?: string }): Promise<BlocksPage> => {
    const cat = opts.categories?.[0]
    const q = (opts.search || '').toLowerCase()
    const items = BLOCKS.filter((b) => (!cat || b.category === cat)
      && (!q || `${b.name} ${b.text}`.toLowerCase().includes(q)))
    return { items, total: items.length, page: 1, per_page: 48 }
  })
  const listCategories = vi.fn(async (): Promise<CategoryCount[]> => CATS)
  const listTags = vi.fn(async (): Promise<TagCount[]> => [])
  const resolveBlocks = vi.fn(async (ids: string[]): Promise<LibraryBlock[]> => BLOCKS.filter((b) => ids.includes(b.id)))
  return { listBlocks, listCategories, listTags, resolveBlocks, favorites: () => favorites }
}

const tick = async () => { for (let i = 0; i < 4; i++) await Promise.resolve() }

describe('usePromptLibrary — browse path', () => {
  it('lists everything on activate, with rail counts and an all-count', async () => {
    const d = deps([])
    const lib = usePromptLibrary(d)
    lib.activate(); await tick()
    expect(d.listBlocks).toHaveBeenCalledWith(expect.objectContaining({ categories: [] }))
    expect(lib.items.value).toHaveLength(3)
    expect(lib.allCount.value).toBe(3) // sum of category counts
    expect(lib.categories.value.map((c) => c.slug)).toEqual(['body', 'scene-lighting'])
  })

  it('single-select category narrows the query and clears tag pins', async () => {
    const d = deps([])
    const lib = usePromptLibrary(d)
    lib.activate(); await tick()
    lib.tags.value = ['figure']
    lib.setCategory('body'); await tick()
    expect(lib.category.value).toBe('body')
    expect(lib.tags.value).toEqual([]) // category switch resets tags
    expect(d.listBlocks).toHaveBeenLastCalledWith(expect.objectContaining({ categories: ['body'] }))
    expect(lib.items.value.map((b) => b.id)).toEqual(['b1', 'b2'])
  })

  it('narrows the tag pins to the search results (not the whole category vocabulary)', async () => {
    const d = deps([])
    const lib = usePromptLibrary(d)
    lib.activate(); await tick()
    d.listTags.mockClear()
    lib.setSearch('curvy'); await new Promise((r) => setTimeout(r, 320)); await tick()
    expect(lib.items.value.map((b) => b.id)).toEqual(['b1'])
    expect(lib.tagOptions.value.map((t) => t.name)).toEqual(['figure']) // only tags of the matching block
    expect(d.listTags).not.toHaveBeenCalled() // backend tag vocab is skipped while searching
  })
})

describe('usePromptLibrary — favorites path', () => {
  it('resolves the favorites and filters them client-side; rail counts are fav-derived', async () => {
    const d = deps(['b1', 'b3']) // one body, one lighting
    const lib = usePromptLibrary(d)
    lib.activate(); await tick()
    lib.setFavOnly(true); await tick()
    expect(d.resolveBlocks).toHaveBeenCalledWith(['b1', 'b3'])
    expect(lib.items.value.map((b) => b.id).sort()).toEqual(['b1', 'b3'])
    expect(lib.allCount.value).toBe(2)
    // only categories that actually hold a favorite show, with the favorite count
    expect(lib.categories.value.map((c) => [c.slug, c.count])).toEqual([['body', 1], ['scene-lighting', 1]])
  })

  it('ANDs the favorites filter with the selected category', async () => {
    const d = deps(['b1', 'b2', 'b3'])
    const lib = usePromptLibrary(d)
    lib.setFavOnly(true); await tick()
    lib.setCategory('body'); await tick()
    expect(lib.items.value.map((b) => b.id)).toEqual(['b1', 'b2']) // favorites, narrowed to Body
    expect(d.listBlocks).not.toHaveBeenCalled() // favorites path never hits the paged endpoint
  })
})
