/* Data layer for the prompt widget's Find-in-Library mode: race-guarded, debounced search over
 * the vault's blocks with category/tag filters and page-append (infinite scroll). Deps are
 * injectable so the logic is unit-testable without a network (the component passes the real
 * api.ts functions). */
import { ref, type Ref } from 'vue'
import { ApiError, type BlockSort } from '../api'
import type { BlocksPage, CategoryCount, LibraryBlock, TagCount } from '../types'

export interface BrowseDeps {
  listBlocks: (opts: { categories?: string[]; tags?: string[]; search?: string; sort?: BlockSort; page?: number; perPage?: number }) => Promise<BlocksPage>
  listCategories: (tags?: string[]) => Promise<CategoryCount[]>
  listTags: (category?: string) => Promise<TagCount[]>
}

const PER_PAGE = 48
const DEBOUNCE_MS = 300

export function usePromptBrowse(deps: BrowseDeps) {
  const search = ref('')
  const categories_sel: Ref<string[]> = ref([]) // multi-select; [] = all
  const sort = ref<BlockSort>('updated')
  const tags = ref<string[]>([])
  const items: Ref<LibraryBlock[]> = ref([])
  const total = ref(0)
  const categories: Ref<CategoryCount[]> = ref([])
  const tagOptions: Ref<TagCount[]> = ref([])
  const loading = ref(false)
  const noVault = ref(false)

  let page = 1
  let req = 0 // request token: a slow earlier response must never overwrite a newer one
  let debounceTimer: ReturnType<typeof setTimeout> | null = null

  async function load(append = false) {
    const token = ++req
    loading.value = true
    if (!append) page = 1
    try {
      const res = await deps.listBlocks({
        categories: categories_sel.value, tags: tags.value, sort: sort.value,
        search: search.value.trim() || undefined, page, perPage: PER_PAGE,
      })
      if (token !== req) return // superseded
      items.value = append ? [...items.value, ...res.items] : res.items
      total.value = res.total
      noVault.value = false
    } catch (e) {
      if (token !== req) return
      if (e instanceof ApiError && e.status === 409) noVault.value = true
      items.value = []
      total.value = 0
    } finally {
      if (token === req) loading.value = false
    }
  }

  async function refreshFilters() {
    try {
      categories.value = await deps.listCategories(tags.value) // counts reflect the active tag filter
      noVault.value = false
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) noVault.value = true
      categories.value = []
    }
    try {
      // Tag options re-scope only when exactly one category is selected — multi keeps the full set.
      tagOptions.value = await deps.listTags(categories_sel.value.length === 1 ? categories_sel.value[0] : '')
    } catch {
      tagOptions.value = []
    }
  }

  // Entering browse mode: (re)load everything — filters persist within the session, never to disk.
  function activate() {
    refreshFilters()
    load()
  }

  function setSearch(value: string) {
    search.value = value
    if (debounceTimer) clearTimeout(debounceTimer)
    debounceTimer = setTimeout(() => load(), DEBOUNCE_MS)
  }

  function toggleCategory(slug: string) {
    if (!slug) categories_sel.value = [] // 'All' resets the multi-select
    else {
      const i = categories_sel.value.indexOf(slug)
      if (i >= 0) categories_sel.value.splice(i, 1)
      else categories_sel.value.push(slug)
    }
    load()
    refreshFilters() // tag options re-scope to the selection
  }

  function setSort(value: BlockSort) {
    if (sort.value === value) return
    sort.value = value
    load()
  }

  function toggleTag(name: string) {
    const i = tags.value.indexOf(name)
    if (i >= 0) tags.value.splice(i, 1)
    else tags.value.push(name)
    load()
    refreshFilters() // category counts re-scope to the tag filter
  }

  function clearFilters() {
    search.value = ''
    categories_sel.value = []
    tags.value = []
    activate()
  }

  // Infinite scroll: append the next page while there is more.
  function loadMore() {
    if (loading.value || items.value.length >= total.value) return
    page += 1
    load(true)
  }

  function dispose() {
    if (debounceTimer) clearTimeout(debounceTimer)
  }

  return {
    search, selectedCats: categories_sel, sort, tags, items, total, categories, tagOptions, loading, noVault,
    activate, refreshFilters, setSearch, toggleCategory, setSort, toggleTag, clearFilters, loadMore, dispose,
  }
}
