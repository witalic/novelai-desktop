/* Album-membership operations over a work's gallery — the single home for the "each gallery image lives
 * in exactly one grid" invariant, shared by the Generate canvas and the Works editor so a third host can
 * never reintroduce the divergence that produced the delete/ghost bugs. Pure (dict in, dict out) → tested. */
import type { GalleryBlock, GalleryGridBlock, WorkDoc } from '../types'

export function gridsOf(blocks: GalleryBlock[] | undefined): GalleryGridBlock[] {
  return (blocks || []).filter((b): b is GalleryGridBlock => b.type === 'grid')
}

/** Remove an image id from every grid album it appears in (a gallery image belongs to at most one grid). */
export function removeIdFromGrids(blocks: GalleryBlock[] | undefined, id: string): void {
  for (const g of gridsOf(blocks)) {
    const i = g.imageIds.indexOf(id)
    if (i >= 0) g.imageIds.splice(i, 1)
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function galleryBlocksOf(doc: WorkDoc): GalleryBlock[] | undefined {
  const zone = (doc.canvas?.nodes || []).find((n) => n.type === 'zone' && (n.data as { role?: string })?.role === 'gallery')
  return zone ? ((zone.data as { blocks?: GalleryBlock[] }).blocks) : undefined
}

/** Delete images from the work in ALL structures — the images list, every grid album, and the canvas
 *  node graph — and promote the next gallery image if the preview pointed at a deleted one. */
export function purgeImages(doc: WorkDoc, ids: string[]): void {
  const set = new Set(ids)
  const blocks = galleryBlocksOf(doc)
  for (const id of ids) removeIdFromGrids(blocks, id)
  doc.images = doc.images.filter((i) => !set.has(i.id))
  if (doc.canvas?.nodes) doc.canvas.nodes = doc.canvas.nodes.filter((n) => !(n.type === 'image' && set.has(n.id)))
  if (doc.preview_image_id && set.has(doc.preview_image_id)) {
    doc.preview_image_id = doc.images.find((i) => i.role === 'gallery')?.id ?? null
  }
}
