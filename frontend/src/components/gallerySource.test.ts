import { describe, expect, it } from 'vitest'
import { filterBySource, hiddenBlockIds } from './gallerySource'

const img = (id: string, favorite = false, tags: string[] = [], group: string | null = null) =>
  ({ id, data: { favorite, tags, group } })

const IMAGES = [
  img('a', true, ['portrait', 'rim light'], 'batch-1'),
  img('b', false, ['exploration'], 'batch-1'),
  img('c', true, ['portrait'], 'batch-2'),
  img('d', false, [], null),
]

describe('filterBySource', () => {
  it('"all" (and unknown sources) returns everything', () => {
    expect(filterBySource(IMAGES, 'all').map((i) => i.id)).toEqual(['a', 'b', 'c', 'd'])
    expect(filterBySource(IMAGES, 'whatever').map((i) => i.id)).toEqual(['a', 'b', 'c', 'd'])
  })

  it('"favorites" keeps only starred images', () => {
    expect(filterBySource(IMAGES, 'favorites').map((i) => i.id)).toEqual(['a', 'c'])
  })

  it('"tag:x" keeps images carrying that tag', () => {
    expect(filterBySource(IMAGES, 'tag:portrait').map((i) => i.id)).toEqual(['a', 'c'])
    expect(filterBySource(IMAGES, 'tag:missing')).toEqual([])
  })

  it('"group:g" keeps images in that group', () => {
    expect(filterBySource(IMAGES, 'group:batch-1').map((i) => i.id)).toEqual(['a', 'b'])
  })
})

describe('hiddenBlockIds', () => {
  const blk = (id: string, type: string, collapsed = false) => ({ id, type, collapsed })

  it('hides nothing when no section is collapsed', () => {
    const blocks = [blk('s1', 'section'), blk('g1', 'grid'), blk('t1', 'text')]
    expect([...hiddenBlockIds(blocks)]).toEqual([])
  })

  it('hides blocks after a collapsed section up to the next section (headers always show)', () => {
    const blocks = [
      blk('h', 'heading'),
      blk('s1', 'section', true), blk('g1', 'grid'), blk('t1', 'text'),
      blk('s2', 'section', false), blk('g2', 'grid'),
    ]
    expect([...hiddenBlockIds(blocks)]).toEqual(['g1', 't1']) // s2 (header) + its children stay visible
  })

  it('re-hides after a later collapsed section', () => {
    const blocks = [blk('s1', 'section', false), blk('g1', 'grid'), blk('s2', 'section', true), blk('g2', 'grid')]
    expect([...hiddenBlockIds(blocks)]).toEqual(['g2'])
  })
})
