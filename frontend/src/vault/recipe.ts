/* An image's recipe for the viewer's Recipe panel (useImagePreview `Recipe`): the prompt as sent, the blocks it was
 * made of in their Library colours, its settings in the catalog's words, its tags. Built the same way whether the
 * image comes from the gallery's API or from a snapshot on the canvas. */
import { listCategories } from '../api'
import { modelLabel, samplerLabel } from '../composables/useCatalog'
import type { Recipe } from '../composables/useImagePreview'
import type { GalleryAlbum, ImageNodeData, PersistedComponent } from '../types'

// The Library's category colours, read once a minute at most — the panel asks per image.
let colors: { at: number; get: Promise<Map<string, string>> } | null = null
export async function categoryColors(): Promise<(slug: string) => string | undefined> {
  if (!colors || Date.now() - colors.at > 60_000) {
    colors = { at: Date.now(), get: listCategories().then((cs) => new Map(cs.map((c) => [c.slug, c.color])), () => new Map()) }
  }
  const known = await colors.get
  return (slug) => known.get(slug)
}

/** The recipe of an image on a work's canvas (its node data), for a work's own viewers. */
export async function imageRecipe(data: Pick<Partial<ImageNodeData>, 'snapshot' | 'tags'> | undefined,
  ctx: { work?: string; where?: string }): Promise<Recipe | null> {
  const snap = data?.snapshot
  if (!snap) return null
  const blockTags = snap.components.filter((c) => c.polarity !== 'negative').flatMap((c) => c.tags)
  return recipeOf({ positive: snap.positive, negative: snap.negative, components: snap.components, params: snap.params,
    tags: [...(data?.tags || []), ...blockTags] }, { ...ctx, colorOf: await categoryColors() })
}

export interface RecipeSource {
  positive: string
  negative: string
  components: PersistedComponent[]
  params: Record<string, unknown>
  tags: string[] // the image's own, then its blocks'
}

/** Where an image sits in its work's gallery, in words: Section “Night” · “Harvest moon” · album 2. */
export function placeLabel(album: GalleryAlbum | null): string {
  if (!album) return 'Quick access (unsorted)'
  return [album.section && `Section “${album.section}”`, album.heading && `“${album.heading}”`, `album ${album.number}`]
    .filter(Boolean).join(' · ')
}

export function recipeOf(src: RecipeSource, ctx: { work?: string; where?: string; colorOf?: (slug: string) => string | undefined } = {}): Recipe {
  const p = src.params
  const num = (k: string) => (typeof p[k] === 'number' ? (p[k] as number) : null)
  const str = (k: string) => (typeof p[k] === 'string' ? (p[k] as string) : null)
  const settings: [string, string][] = []
  if (str('model')) settings.push(['Model', modelLabel(str('model')!)])
  if (num('width') && num('height')) settings.push(['Size', `${num('width')}×${num('height')}`])
  if (num('steps') !== null || num('scale') !== null) settings.push(['Steps · Guidance', `${num('steps') ?? '—'} · ${num('scale') ?? '—'}`])
  if (str('sampler')) settings.push(['Sampler', [samplerLabel(str('sampler')!), str('noise_schedule')].filter(Boolean).join(' · ')])
  if (num('seed') !== null) settings.push(['Seed', String(num('seed'))])
  return {
    work: ctx.work, where: ctx.where, positive: src.positive, negative: src.negative,
    blocks: src.components.map((c) => ({ name: c.name || c.text.slice(0, 24), color: c.category ? ctx.colorOf?.(c.category) : undefined,
      negative: c.polarity === 'negative' })),
    settings, tags: [...new Set(src.tags)],
  }
}
