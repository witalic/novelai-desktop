/* Data layer for the prompt widget's direct Library browser (design/prompt-widget-rework-mockup.html).
 * Single-select category (the rail), tag multi-select, a debounced search over name+prompt+tags, and a
 * per-work favorites filter. Race-guarded so a slow earlier response never overwrites a newer one.
 *
 * Two data paths:
 *  - normal: page the vault via listBlocks (infinite scroll), rail counts from listCategories.
 *  - favorites: the work's favorites is a small id set, not a backend query — resolve those blocks
 *    (resolveBlocks) once and filter them client-side; the rail counts are derived from that set.
 *
 * Deps are injectable so the logic is unit-testable without a network (the component passes api.ts). */
import { ref, type Ref } from 'vue'
import { ApiError } from '../api'
import type { BlocksPage, CategoryCount, LibraryBlock, TagCount } from '../types'

export interface LibraryDeps {
  listBlocks: (opts: { categories?: string[]; tags?: string[]; search?: string; page?: number; perPage?: number }) => Promise<BlocksPage>
  listCategories: (tags?: string[]) => Promise<CategoryCount[]>
  listTags: (category?: string) => Promise<TagCount[]>
  resolveBlocks: (ids: string[]) => Promise<LibraryBlock[]>
  favorites: () => string[] // the work's starred Library block ids (reactive source in the component)
}

const PER_PAGE = 48
const DEBOUNCE_MS = 300

export function usePromptLibrary(deps: LibraryDeps) {
  const search = ref('')
  const category = ref('')        // single-select rail: '' = all
  const tags = ref<string[]>([])
  const favOnly = ref(false)      // ANDs with the category
  const items: Ref<LibraryBlock[]> = ref([])
  const total = ref(0)
  const allCount = ref(0)         // blocks in the current view before the category narrows it (rail "All")
  const categories: Ref<CategoryCount[]> = ref([]) // rail palette: slug/name/color + count (view-scoped)
  const tagOptions: Ref<TagCount[]> = ref([])      // tag pins for the current category
  const loading = ref(false)
  const noVault = ref(false)

  let page = 1
  let req = 0
  let debounceTimer: ReturnType<typeof setTimeout> | null = null
  let favCache: LibraryBlock[] = [] // resolved favorite blocks (favOnly path), refreshed per load

  function matches(b: LibraryBlock): boolean {
    const q = search.value.trim().toLowerCase()
    return (!category.value || b.category === category.value)
      && tags.value.every((t) => b.tags.includes(t))
      && (!q || `${b.name} ${b.text} ${b.tags.join(' ')}`.toLowerCase().includes(q))
  }

  // Tag pins derived from a block set (used when a search is active — the pins narrow to the results).
  function tagsFromBlocks(blocks: LibraryBlock[]): TagCount[] {
    const c: Record<string, number> = {}
    for (const b of blocks) for (const t of b.tags) c[t] = (c[t] || 0) + 1
    return Object.entries(c).sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count }))
  }

  async function resolveFavs(): Promise<void> {
    const ids = deps.favorites()
    if (!ids.length) { favCache = []; return }
    try { favCache = await deps.resolveBlocks(ids) } catch { favCache = [] }
  }

  async function load(append = false): Promise<void> {
    const token = ++req
    loading.value = true
    if (!append) page = 1
    try {
      if (favOnly.value) {
        await resolveFavs()
        if (token !== req) return
        const filtered = favCache.filter(matches)
        items.value = filtered
        total.value = filtered.length
        if (search.value.trim()) tagOptions.value = tagsFromBlocks(filtered) // search narrows the tag pins
        noVault.value = false
      } else {
        const res = await deps.listBlocks({
          categories: category.value ? [category.value] : [], tags: tags.value,
          search: search.value.trim() || undefined, page, perPage: PER_PAGE,
        })
        if (token !== req) return
        items.value = append ? [...items.value, ...res.items] : res.items
        total.value = res.total
        if (!append && search.value.trim()) tagOptions.value = tagsFromBlocks(items.value) // search narrows the tag pins
        noVault.value = false
      }
    } catch (e) {
      if (token !== req) return
      if (e instanceof ApiError && e.status === 409) noVault.value = true
      items.value = []
      total.value = 0
    } finally {
      if (token === req) loading.value = false
    }
  }

  // Rail categories/counts + tag pins. When favorites is on, both are derived from the resolved
  // favorite set (using the backend palette for names/colors); otherwise straight from the backend.
  async function refreshFilters(): Promise<void> {
    try {
      const cats = await deps.listCategories(tags.value) // names + colors + full-pool counts (tag-scoped)
      if (favOnly.value) {
        await resolveFavs()
        const inTags = (b: LibraryBlock) => tags.value.every((t) => b.tags.includes(t))
        const counts: Record<string, number> = {}
        for (const b of favCache) if (inTags(b)) counts[b.category] = (counts[b.category] || 0) + 1
        categories.value = cats.map((c) => ({ ...c, count: counts[c.slug] || 0 })).filter((c) => c.count > 0)
        allCount.value = favCache.filter(inTags).length
      } else {
        categories.value = cats
        allCount.value = cats.reduce((n, c) => n + c.count, 0)
      }
      noVault.value = false
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) noVault.value = true
      categories.value = []
      allCount.value = 0
    }
    try {
      if (search.value.trim()) return // a search is active — load() owns the (results-scoped) tag pins
      if (favOnly.value) {
        await resolveFavs()
        const counts: Record<string, number> = {}
        for (const b of favCache) if (!category.value || b.category === category.value) for (const t of b.tags) counts[t] = (counts[t] || 0) + 1
        tagOptions.value = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count }))
      } else {
        tagOptions.value = await deps.listTags(category.value)
      }
    } catch {
      tagOptions.value = []
    }
  }

  // Reload everything (list + rail). Filters persist within the session, never to disk.
  function reload(): void {
    load()
    refreshFilters()
  }
  const activate = reload

  function setSearch(value: string): void {
    const wasActive = !!search.value.trim()
    search.value = value
    if (debounceTimer) clearTimeout(debounceTimer)
    // reload() (not load) so the tag pins re-scope: to the results while searching, back to the
    // category vocabulary once the box is cleared.
    debounceTimer = setTimeout(() => (value.trim() || wasActive ? reload() : load()), DEBOUNCE_MS)
  }

  function setCategory(slug: string): void {
    if (category.value === slug) return
    category.value = slug
    tags.value = [] // category switch clears the tag pins (they're scoped to the category)
    reload()
  }

  function toggleTag(name: string): void {
    const i = tags.value.indexOf(name)
    if (i >= 0) tags.value.splice(i, 1)
    else tags.value.push(name)
    reload() // category counts re-scope to the tag filter
  }

  function setFavOnly(on: boolean): void {
    if (favOnly.value === on) return
    favOnly.value = on
    reload()
  }

  function clearFilters(): void {
    search.value = ''
    category.value = ''
    tags.value = []
    favOnly.value = false
    reload()
  }

  // Infinite scroll (normal path only — the favorites path is fully client-side, no paging).
  function loadMore(): void {
    if (favOnly.value || loading.value || items.value.length >= total.value) return
    page += 1
    load(true)
  }

  // Favorites changed under us (star toggled elsewhere / work reopened): refresh if it can matter.
  function onFavoritesChanged(): void {
    if (favOnly.value) reload()
  }

  function dispose(): void {
    if (debounceTimer) clearTimeout(debounceTimer)
  }

  return {
    search, category, tags, favOnly, items, total, allCount, categories, tagOptions, loading, noVault,
    activate, reload, refreshFilters, setSearch, setCategory, toggleTag, setFavOnly, clearFilters,
    loadMore, onFavoritesChanged, dispose,
  }
}
