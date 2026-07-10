import { describe, expect, it } from 'vitest'
import { canvasToWork, workToCanvas, GALLERY, STATION } from './serialize'

/* serialize.ts is framework-free by design — these pin the canvas <-> WorkDoc contract:
 * geometry/parent/snapshot survive the round-trip, aspect ratio is recovered, snapshots dedup by
 * prompt+params, image bytes split correctly, and transient UI flags never reach disk. */

function anchors() {
  return [
    { id: STATION, type: 'station', position: { x: 316, y: 40 }, style: { width: '760px', height: '460px' }, data: { outputRatio: 0.3, posRatio: 0.5 } },
    { id: GALLERY, type: 'zone', position: { x: 1108, y: 40 }, style: { width: '320px', height: '440px' }, data: { role: 'gallery' } },
  ]
}

function galleryImage(id: string, seed: number, url = `/api/vault/works/w1/images/${id}`) {
  return {
    id, type: 'image', parentNode: GALLERY, position: { x: 12, y: 44 }, style: { width: '102px', height: '180px' },
    data: {
      url, file: `images/${id}.png`, created_at: '2026-01-01',
      snapshot: { components: [{ polarity: 'positive', text: '1girl' }], positive: '1girl', negative: '',
        params: { width: 832, height: 1216, seed }, hash: 'positive:1girl' },
    },
  }
}

const params = { width: 832, height: 1216, seed: null, steps: 28 }
const viewport = { x: 40, y: 40, zoom: 0.7 }

describe('canvasToWork', () => {
  it('splits a vault-path image into a file ref (no base64) and keeps its recipe', () => {
    const doc = canvasToWork([...anchors(), galleryImage('img-1', 42)], viewport, params, { id: 'w1', title: 'Test' })
    expect(doc.id).toBe('w1')
    expect(doc.title).toBe('Test')
    expect(doc.images).toHaveLength(1)
    expect(doc.images[0]).toMatchObject({ id: 'img-1', file: 'images/img-1.png', image_b64: null })
    expect(doc.snapshots).toHaveLength(1)
    expect(doc.snapshots[0].params.seed).toBe(42)
    expect(doc.preview_image_id).toBe('img-1')
  })

  it('encodes a freshly generated data: URL as base64 (no file)', () => {
    const im = galleryImage('img-2', 7, 'data:image/png;base64,QUJD')
    const doc = canvasToWork([...anchors(), im], viewport, params, { id: 'w1', title: '' })
    expect(doc.images[0]).toMatchObject({ file: '', image_b64: 'QUJD' })
  })

  it('dedups snapshots by prompt AND params — same prompt, different seed → two recipes', () => {
    const doc = canvasToWork([...anchors(), galleryImage('a', 1), galleryImage('b', 2)], viewport, params, { id: 'w1', title: '' })
    expect(doc.snapshots).toHaveLength(2)
    expect(doc.images.map((i: any) => i.snapshot_id)).toEqual([doc.snapshots[0].id, doc.snapshots[1].id])
  })

  it('does not persist transient block UI flags', () => {
    const block = {
      id: 'block-1', type: 'block', parentNode: STATION, position: { x: 400, y: 80 }, style: { width: '176px' },
      data: { name: 'Char', text: '1girl', polarity: 'positive', category: 'character', block_id: 'b1', version: 1, tags: ['x'], expanded: true, editing: true },
    }
    const doc = canvasToWork([...anchors(), block], viewport, params, { id: 'w1', title: '' })
    const saved = doc.canvas.nodes.find((n: any) => n.id === 'block-1')!
    expect(saved.data.text).toBe('1girl')
    expect(saved.data).not.toHaveProperty('expanded')
    expect(saved.data).not.toHaveProperty('editing')
  })
})

describe('round-trip (canvasToWork -> workToCanvas)', () => {
  it('preserves geometry + parent, restores the snapshot, and recovers aspect ratio from params', () => {
    const doc = canvasToWork([...anchors(), galleryImage('img-1', 42)], viewport, params, { id: 'w1', title: 'Test' })
    const { nodes, viewport: vp } = workToCanvas(doc)
    expect(vp).toEqual(viewport)
    const img = nodes.find((n: any) => n.id === 'img-1')!
    expect(img.parentNode).toBe(GALLERY)
    expect(img.position).toEqual({ x: 12, y: 44 })
    expect(img.style).toEqual({ width: '102px', height: '180px' })
    expect(img.data.url).toBe('/api/vault/works/w1/images/img-1')
    expect(img.data.snapshot.params.seed).toBe(42)   // snapshot restored onto the node (re-save keeps it)
    expect(img.data.ar).toBeCloseTo(832 / 1216)       // recovered from params, not the rounded node size
  })
})
