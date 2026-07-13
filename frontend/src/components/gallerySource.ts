// An image-grid block picks its images by a saved query string, never a stored id list — so new
// images flow in and one image can appear in several grids. Kept framework-free for unit testing.
export interface GalleryImageLike {
  data: { favorite?: boolean; tags?: string[]; group?: string | null }
}

export function filterBySource<T extends GalleryImageLike>(images: T[], source: string): T[] {
  if (source === 'favorites') return images.filter((i) => !!i.data.favorite)
  if (source.startsWith('tag:')) { const t = source.slice(4); return images.filter((i) => (i.data.tags || []).includes(t)) }
  if (source.startsWith('group:')) { const g = source.slice(6); return images.filter((i) => i.data.group === g) }
  return images // 'all' (and any unknown source) shows everything
}
