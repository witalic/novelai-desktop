import { describe, expect, it } from 'vitest'
import { groupByCategory } from './librarySections'
import type { LibraryBlock } from '../types'

const block = (id: string, category: string): LibraryBlock => ({
  id, category, name: id, text: id, polarity: 'positive', tags: [],
})

describe('groupByCategory', () => {
  it('returns no sections for an empty page', () => {
    expect(groupByCategory([])).toEqual([])
  })

  it('makes one section per consecutive category run', () => {
    const sections = groupByCategory([
      block('a', 'character'), block('b', 'character'),
      block('c', 'body'), block('d', 'body'), block('e', 'body'),
      block('f', 'style'),
    ])
    expect(sections.map((s) => [s.slug, s.items.length])).toEqual([
      ['character', 2], ['body', 3], ['style', 1],
    ])
  })

  it('keeps a category contiguous when a later page continues it (accumulation)', () => {
    // page 1 ended mid-"body"; page 2 opens with more "body" — must stay one section, not split.
    const page1 = [block('a', 'character'), block('b', 'body'), block('c', 'body')]
    const page2 = [block('d', 'body'), block('e', 'style')]
    const sections = groupByCategory([...page1, ...page2])
    expect(sections.map((s) => s.slug)).toEqual(['character', 'body', 'style'])
    expect(sections[1].items.map((b) => b.id)).toEqual(['b', 'c', 'd'])
  })

  it('does not merge non-adjacent runs of the same category', () => {
    // sort=category guarantees adjacency, but the helper must not globally coalesce if it ever isn't.
    const sections = groupByCategory([
      block('a', 'body'), block('b', 'style'), block('c', 'body'),
    ])
    expect(sections.map((s) => s.slug)).toEqual(['body', 'style', 'body'])
  })
})
