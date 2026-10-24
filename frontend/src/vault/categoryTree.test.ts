import { describe, expect, it } from 'vitest'
import { buildCategoryTree, groupDot, groupOf, labelOf, nonEmpty, scopeOf, tagsInScope } from './categoryTree'

const cats = [
  { slug: 'character', name: 'Character', color: '#00f', count: 10 },
  { slug: 'body', name: 'Body', color: '#c77', count: 12 },
  { slug: 'body-skin', name: 'Body - Skin', color: '#ea7', count: 0 },
  { slug: 'body-face', name: 'Body - Face', color: '#d97', count: 3 },
  { slug: 'scene-camera', name: 'Scene - Camera', color: '#1b6', count: 10 },
  { slug: 'scene-color', name: 'Scene - Color', color: '#3f6', count: 1 },
  { slug: 'composition', name: 'Composition', color: '#75a', count: 0 },
  { slug: 'cat-ab12', name: 'Body - Tattoo', color: '#a55', count: 2 }, // a custom category nests by its name
]
const catName = (slug: string) => cats.find((c) => c.slug === slug)?.name ?? slug

describe('category tree', () => {
  const tree = buildCategoryTree(cats)

  it('groups subgroups under their parent by name, in vault order', () => {
    expect(tree.map((g) => g.name)).toEqual(['Character', 'Body', 'Scene', 'Composition'])
    const body = tree[1]
    expect(body.key).toBe('group:body')
    expect(body.count).toBe(17)
    expect(body.slugs).toEqual(['body', 'body-skin', 'body-face', 'cat-ab12'])
  })

  it('lists a group’s own category first among its subgroups, as "General"', () => {
    const body = tree[1]
    expect(body.children.map((c) => [c.short, c.slug, !!c.own])).toEqual([
      ['General', 'body', true], ['Skin', 'body-skin', false], ['Face', 'body-face', false], ['Tattoo', 'cat-ab12', false],
    ])
    expect(tree[0]).toMatchObject({ key: 'character', children: [] }) // a lone category is its own key
  })

  it('makes a pure group of a parent that is no category itself', () => {
    const scene = tree[2]
    expect(scene).toMatchObject({ key: 'group:scene', self: null, color: null, count: 11 })
    expect(scene.children.some((c) => c.own)).toBe(false)
    expect(groupDot(scene)).toContain('conic-gradient(#1b6')
  })

  it('hides empty groups and subgroups for filter rails — never the selection', () => {
    const shown = nonEmpty(tree)
    expect(shown.map((g) => g.name)).toEqual(['Character', 'Body', 'Scene'])
    expect(shown[1].children.map((c) => c.short)).toEqual(['General', 'Face', 'Tattoo'])
    expect(nonEmpty(tree, 'composition').map((g) => g.name)).toContain('Composition')
    expect(nonEmpty(tree, 'body-skin')[1].children.map((c) => c.short)).toEqual(['General', 'Skin', 'Face', 'Tattoo'])
  })

  it('filters a group by all of its categories, and "General" or a subgroup by itself', () => {
    expect(scopeOf(tree, '')).toEqual([])
    expect(scopeOf(tree, 'group:body')).toEqual(['body', 'body-skin', 'body-face', 'cat-ab12'])
    expect(scopeOf(tree, 'body')).toEqual(['body']) // just Body — the owner's "суто body"
    expect(scopeOf(tree, 'group:scene')).toEqual(['scene-camera', 'scene-color'])
    expect(scopeOf(tree, 'body-face')).toEqual(['body-face'])
    expect(groupOf(tree, 'body-face')?.key).toBe('group:body')
    expect(groupOf(tree, 'body')?.key).toBe('group:body')
  })

  it('names a selection so the group and its own category can be told apart', () => {
    expect(labelOf(tree, 'group:body', catName)).toBe('Body')
    expect(labelOf(tree, 'body', catName)).toBe('Body · General')
    expect(labelOf(tree, 'body-face', catName)).toBe('Body - Face')
    expect(labelOf(tree, 'character', catName)).toBe('Character')
  })

  it('sums a group’s tag pins across its categories', async () => {
    const lists: Record<string, { name: string; count: number }[]> = {
      'scene-camera': [{ name: 'pov', count: 4 }, { name: 'dutch', count: 1 }],
      'scene-color': [{ name: 'pov', count: 1 }],
    }
    const tags = await tagsInScope(['scene-camera', 'scene-color'], async (c) => lists[c] ?? [])
    expect(tags).toEqual([{ name: 'pov', count: 5 }, { name: 'dutch', count: 1 }])
    expect(await tagsInScope([], async (c) => [{ name: c || 'all', count: 1 }])).toEqual([{ name: 'all', count: 1 }])
  })
})
