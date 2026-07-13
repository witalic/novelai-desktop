import { describe, expect, it } from 'vitest'
import { hiddenBlockIds } from './galleryBlocks'

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
