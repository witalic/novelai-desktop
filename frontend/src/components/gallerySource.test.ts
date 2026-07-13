import { describe, expect, it } from 'vitest'
import { filterBySource } from './gallerySource'

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
