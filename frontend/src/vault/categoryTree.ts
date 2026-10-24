/* Two-level category groups (design/ui-polish.html §9), derived from the names: "Body - Skin" is the
 * subgroup "Skin" of "Body", so a new "Body - Tattoo" nests by itself — no extra data. A group whose name
 * is no category of its own ("Scene") is a pure group. Order follows the vault's category order.
 *
 * Selecting a group filters the whole group. A group that is also a category AND has subgroups lists that
 * category first among its subgroups as "General" — so "Body" alone can be looked at too (owner request).
 *
 * Rails FILTER by it and hide what's empty (`nonEmpty`); pickers CHOOSE from it and show every group. */
import type { TagCount } from '../types'

export interface CatNode { slug: string; name: string; color: string; count: number }
export interface CatChild extends CatNode {
  short: string
  own?: boolean // the group's own category, listed as "General" (pickers skip it — the group row picks it)
}
export interface CatGroup {
  key: string // `group:<name>` for a group with subgroups; a lone category's own slug
  name: string
  color: string | null // the parent's colour; null for a pure group (draw it from its children's)
  self: CatNode | null // the parent category itself, when there is one
  children: CatChild[]
  count: number // blocks in the whole group
  slugs: string[] // every category in the group — what selecting the group filters by
}

const SEP = ' - '
export const OWN_LABEL = 'General'

function split(name: string): { parent: string; short: string } | null {
  const i = name.indexOf(SEP)
  return i > 0 ? { parent: name.slice(0, i).trim(), short: name.slice(i + SEP.length).trim() } : null
}

export function buildCategoryTree(cats: CatNode[]): CatGroup[] {
  const byName = new Map<string, CatGroup>()
  const order: CatGroup[] = []
  const groupFor = (name: string): CatGroup => {
    const k = name.toLowerCase()
    let g = byName.get(k)
    if (!g) {
      g = { key: `group:${k}`, name, color: null, self: null, children: [], count: 0, slugs: [] }
      byName.set(k, g)
      order.push(g)
    }
    return g
  }
  for (const c of cats) {
    const s = split(c.name)
    if (s) groupFor(s.parent).children.push({ ...c, short: s.short })
    else Object.assign(groupFor(c.name), { self: c, color: c.color, name: c.name })
  }
  for (const g of order) {
    if (g.self && !g.children.length) g.key = g.self.slug // a lone category: no group to tell apart from it
    g.count = (g.self?.count ?? 0) + g.children.reduce((n, c) => n + c.count, 0)
    g.slugs = [...(g.self ? [g.self.slug] : []), ...g.children.map((c) => c.slug)]
    if (g.self && g.children.length) g.children.unshift({ ...g.self, short: OWN_LABEL, own: true })
  }
  return order
}

// For filter rails: groups with no blocks at all go, and so do empty subgroups — except the current
// selection (`keep`), which never vanishes from under the user when a tag filter empties it.
export function nonEmpty(tree: CatGroup[], keep = ''): CatGroup[] {
  return tree.filter((g) => g.count > 0 || (keep && (g.key === keep || g.slugs.includes(keep))))
    .map((g) => ({ ...g, children: g.children.filter((c) => c.count > 0 || c.slug === keep) }))
}

// What a rail selection filters by: a group → all its categories, a subgroup (or "General") → itself,
// '' → everything ([]).
export function scopeOf(tree: CatGroup[], key: string): string[] {
  if (!key) return []
  return tree.find((g) => g.key === key)?.slugs ?? [key]
}

// The group a key (group key or category slug) belongs to — to open it in the rail.
export function groupOf(tree: CatGroup[], key: string): CatGroup | undefined {
  return tree.find((g) => g.key === key || g.slugs.includes(key))
}

// A selection's display name: a group by its name, its own category as "Body · General", a subgroup by its
// full category name.
export function labelOf(tree: CatGroup[], key: string, catName: (slug: string) => string): string {
  const g = groupOf(tree, key)
  if (g?.key === key) return g.name
  if (g?.self?.slug === key && g.children.length) return `${g.name} · ${OWN_LABEL}`
  return catName(key)
}

// A pure group's dot mixes its children's colours.
export function groupDot(g: CatGroup): string {
  if (g.color) return g.color
  const cols = g.children.map((c) => c.color)
  if (cols.length < 2) return cols[0] ?? '#738496'
  const step = 100 / cols.length
  return `conic-gradient(${cols.map((c, i) => `${c} ${i * step}% ${(i + 1) * step}%`).join(', ')})`
}

// A group's tag pins: one listing per category, counts summed (a block sits in exactly one category).
export async function tagsInScope(scope: string[], listTags: (category: string) => Promise<TagCount[]>): Promise<TagCount[]> {
  if (scope.length <= 1) return listTags(scope[0] ?? '')
  const counts = new Map<string, number>()
  for (const list of await Promise.all(scope.map(listTags))) {
    for (const t of list) counts.set(t.name, (counts.get(t.name) ?? 0) + t.count)
  }
  return [...counts].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
}
