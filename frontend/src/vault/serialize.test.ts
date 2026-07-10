import { describe, expect, it } from 'vitest'
import { canvasToWork, workToCanvas, workToDrafts, GALLERY, STATION } from './serialize'

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

  it('whitelists block data: domain + lane layout survive, every transient flag is stripped', () => {
    const block = {
      id: 'block-1', type: 'block', parentNode: STATION, position: { x: 400, y: 80 }, style: { width: '176px' }, zIndex: 2,
      data: {
        name: 'Char', text: '1girl', polarity: 'positive', category: 'character',
        block_id: 'b1', version: 1, tags: ['x'], xFrac: 0.25, laneFrac: 0.5,
        expanded: true, editing: true, _cw: '176px', _ch: '34px', // transient — must never reach disk
      },
    }
    const doc = canvasToWork([...anchors(), block], viewport, params, { id: 'w1', title: '' })
    const saved = doc.canvas.nodes.find((n) => n.id === 'block-1')!
    expect(saved.data).toEqual({
      name: 'Char', text: '1girl', polarity: 'positive', category: 'character',
      block_id: 'b1', version: 1, tags: ['x'], xFrac: 0.25, laneFrac: 0.5,
    })
    expect(saved.zIndex).toBe(2) // stacking order is layout — it persists
  })

  it('persists image nodes as pure layout (empty data) — domain lives in images[]', () => {
    const doc = canvasToWork([...anchors(), galleryImage('img-1', 42)], viewport, params, { id: 'w1', title: '' })
    const node = doc.canvas.nodes.find((n) => n.id === 'img-1')!
    expect(node.data).toEqual({})
  })
})

describe('workToDrafts', () => {
  it('restores the stack with created_at, params, and snapshot intact', () => {
    const draft = {
      id: 'img-9', url: '/api/vault/works/w1/images/img-9', file: 'images/img-9.png',
      params: { width: 832 } as any, mock: false, created_at: '2026-01-02T10:00:00Z',
      snapshot: { components: [], positive: '1girl', negative: '', params: { width: 832, height: 1216, seed: 5 }, hash: 'positive:1girl' },
    }
    const doc = canvasToWork([...anchors()], viewport, params, { id: 'w1', title: '' }, [draft])
    expect(doc.stack[0].created_at).toBe('2026-01-02T10:00:00Z')
    const restored = workToDrafts(doc)
    expect(restored).toHaveLength(1)
    expect(restored[0].created_at).toBe('2026-01-02T10:00:00Z') // the lossy round-trip this test pins
    expect(restored[0].url).toBe('/api/vault/works/w1/images/img-9')
    expect(restored[0].params.seed).toBe(5)
    expect(restored[0].snapshot?.positive).toBe('1girl')
  })
})

describe('round-trip (canvasToWork -> workToCanvas)', () => {
  it('preserves geometry + parent, restores the snapshot, and recovers aspect ratio from params', () => {
    const doc = canvasToWork([...anchors(), galleryImage('img-1', 42)], viewport, params, { id: 'w1', title: 'Test' })
    const { nodes, viewport: vp } = workToCanvas(doc)
    expect(vp).toEqual(viewport)
    const img = nodes.find((n) => n.id === 'img-1')!
    expect(img.parentNode).toBe(GALLERY)
    expect(img.position).toEqual({ x: 12, y: 44 })
    expect(img.style).toEqual({ width: '102px', height: '180px' })
    expect((img.data as any).url).toBe('/api/vault/works/w1/images/img-1')
    expect((img.data as any).snapshot.params.seed).toBe(42)   // snapshot restored onto the node (re-save keeps it)
    expect((img.data as any).ar).toBeCloseTo(832 / 1216)       // recovered from params, not the rounded node size
  })

  it('is idempotent: canvas -> doc -> canvas -> doc yields the identical document', () => {
    const block = {
      id: 'block-1', type: 'block', parentNode: STATION, position: { x: 400, y: 80 }, style: { width: '176px' }, zIndex: 2,
      data: { name: 'Char', text: '1girl', polarity: 'positive', category: 'character', block_id: 'b1', version: 3, tags: ['x'], xFrac: 0.25, laneFrac: 0.5 },
    }
    const draft = {
      id: 'img-9', url: '/api/vault/works/w1/images/img-9', file: 'images/img-9.png',
      params: {} as any, mock: false, created_at: '2026-01-02T10:00:00Z',
      snapshot: { components: [], positive: 'sky', negative: '', params: { seed: 9 }, hash: 'positive:sky', created_at: '2026-01-02T10:00:00Z' },
    }
    const doc1 = canvasToWork([...anchors(), block, galleryImage('img-1', 42)], viewport, params, { id: 'w1', title: 'T' }, [draft])
    const { nodes, viewport: vp } = workToCanvas(doc1)
    const doc2 = canvasToWork(nodes, vp, params, { id: 'w1', title: 'T' }, workToDrafts(doc1))
    expect(doc2).toEqual(doc1) // no field drifts or drops across a full save/load/save cycle
  })
})
