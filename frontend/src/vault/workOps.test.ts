import { describe, it, expect } from 'vitest'
import { purgeImages, removeIdFromGrids } from './workOps'
import type { WorkDoc } from '../types'

function doc(): WorkDoc {
  return {
    schema_version: 6, id: 'w1', title: '', params: {},
    canvas: {
      viewport: { x: 0, y: 0, zoom: 1 },
      nodes: [
        { id: 'gallery', type: 'zone', position: { x: 0, y: 0 }, data: { role: 'gallery', blocks: [
          { id: 'g1', type: 'grid', imageIds: ['a', 'b'], cols: 3 },
          { id: 'g2', type: 'grid', imageIds: ['c'], cols: 3 },
        ] } },
        { id: 'a', type: 'image', position: { x: 0, y: 0 }, data: {} },
        { id: 'b', type: 'image', position: { x: 0, y: 0 }, data: {} },
        { id: 'c', type: 'image', position: { x: 0, y: 0 }, data: {} },
      ],
    },
    snapshots: [],
    images: [
      { id: 'a', snapshot_id: null, role: 'gallery', ar: null, file: 'a.png', created_at: '', group: null, favorite: false, tags: [], description: '' },
      { id: 'b', snapshot_id: null, role: 'gallery', ar: null, file: 'b.png', created_at: '', group: null, favorite: false, tags: [], description: '' },
      { id: 'c', snapshot_id: null, role: 'gallery', ar: null, file: 'c.png', created_at: '', group: null, favorite: false, tags: [], description: '' },
    ],
    stack: [], favorites: [], preview_image_id: 'a',
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const grids = (d: WorkDoc) => (d.canvas.nodes.find((n) => n.id === 'gallery')!.data as any).blocks

describe('workOps', () => {
  it('removeIdFromGrids drops the id from whichever grid holds it', () => {
    const d = doc()
    removeIdFromGrids(grids(d), 'b')
    expect(grids(d)[0].imageIds).toEqual(['a'])
    expect(grids(d)[1].imageIds).toEqual(['c'])
  })

  it('purgeImages removes from images, grids and canvas nodes at once', () => {
    const d = doc()
    purgeImages(d, ['b'])
    expect(d.images.map((i) => i.id)).toEqual(['a', 'c'])
    expect(grids(d)[0].imageIds).toEqual(['a'])
    expect(d.canvas.nodes.some((n) => n.id === 'b')).toBe(false) // no ghost node
  })

  it('purgeImages promotes the next gallery image when the preview is deleted', () => {
    const d = doc() // preview = 'a'
    purgeImages(d, ['a'])
    expect(d.preview_image_id).toBe('b') // not null — the card keeps a preview
  })

  it('purgeImages nulls the preview only when no gallery images remain', () => {
    const d = doc()
    purgeImages(d, ['a', 'b', 'c'])
    expect(d.preview_image_id).toBeNull()
  })
})
