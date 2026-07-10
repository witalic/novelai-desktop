/* Canvas image-rendering pipeline (extracted from CanvasBoard for testability + focus):
 * reference-scale sizing, server-sized `?w=` thumbnails, DPR/headroom-tuned decode, and a flash-free
 * resolution swap. See the inline notes for why each piece exists (decode-memory pressure, HiDPI, etc.). */
import { computed, ref, watch, type Ref } from 'vue'

/* eslint-disable @typescript-eslint/no-explicit-any */
interface Deps {
  nodes: Ref<any[]>
  findNode: (id: string) => any
  sizeOf: (n: any) => { w: number; h: number }
  dims: (n: any) => { w: number; h: number }
}

export const SCALES = [0.5, 1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 9, 10]
export const PICK_SCALES = SCALES.filter((s) => s <= 5) // the picker offers up to ×5
const BASE_LONG = 180 // the long side at scale ×1
const DEC_FLOOR = 480 // never fetch below this long side (crisp when scaled small)
const DEC_CEIL = 1920 // up to the source's long side — big scales get full detail (backend never upscales)

export function useImagePipeline({ nodes, findNode, sizeOf, dims }: Deps) {
  function snapScale(v: number) {
    return SCALES.reduce((best, s) => (Math.abs(s - v) < Math.abs(best - v) ? s : best), SCALES[0])
  }
  function scaleOf(n: any) {
    const d = dims(n)
    return snapScale(Math.max(d.w, d.h) / BASE_LONG)
  }
  function imgScale(id: string) {
    const n = findNode(id)
    return n ? `×${scaleOf(n)}` : ''
  }

  // The <img> is laid out at a chosen long side (→ decoded at that size) then transform-scaled down to the
  // node box, so the decode never collapses to a tiny display size (which smears when scaled back up). The
  // long side = node size × device-pixel-ratio × a supersample headroom, floored so it can't go tiny and
  // capped at the source resolution. The headroom TAPERS with the image count so a large work (100-200)
  // can't blow Chromium's decode-memory budget (→ downsampled bitmaps → pixelation), while a normal work
  // keeps maximum sharpness. Bucketed so adding/removing one image never re-fetches every derivative.
  const imageCount = computed(() => nodes.value.reduce((c, n) => c + (n.type === 'image' ? 1 : 0), 0))
  const headroom = computed(() => {
    const n = imageCount.value
    return n <= 30 ? 3.5 : n <= 60 ? 2.6 : n <= 120 ? 1.9 : 1.4
  })
  function decodeDims(id: string, data: any) {
    const n = findNode(id)
    const box = n ? sizeOf(n) : { w: 180, h: 320 }
    const ar = data.ar || box.w / box.h || 832 / 1216
    const dpr = Math.min(Math.max(window.devicePixelRatio || 1, 1), 3) // physical pixels per CSS px
    const nl = Math.min(DEC_CEIL, Math.max(DEC_FLOOR, Math.ceil(Math.max(box.w, box.h) * headroom.value * dpr)))
    const nw = ar >= 1 ? nl : Math.round(nl * ar)
    const nh = ar >= 1 ? Math.round(nl / ar) : nl
    return { box, nw, nh }
  }
  function fullStyle(id: string, data: any) {
    const { box, nw, nh } = decodeDims(id, data)
    return { width: `${nw}px`, height: `${nh}px`, transform: `scale(${box.w / nw})`, transformOrigin: 'top left' }
  }
  // Vault-stored images are fetched right-sized (`?w=`) so the browser never decodes the full-resolution
  // source only to shrink it. Freshly generated images are data: URIs and stay as-is.
  function imgSrc(id: string, data: any) {
    if (!data.url || data.url.startsWith('data:')) return data.url
    return `${data.url}?w=${decodeDims(id, data).nw}`
  }

  // Flash-free resolution swap: when a node's target ?w= changes we preload the new derivative off-screen
  // and swap the visible <img> only once it has decoded — the current image stays put meanwhile. `shownSrc`
  // is seeded when a node first appears (seedSrc) so there is always an "old" src to hold during the swap.
  const shownSrc = ref<Record<string, string>>({})
  function seedSrc(id: string) {
    const n = findNode(id)
    if (n && n.type === 'image' && !shownSrc.value[id]) shownSrc.value[id] = imgSrc(id, n.data)
  }
  // Called imperatively from the events that change the target (scale, headroom bucket) — NOT from a deep
  // node watcher, which would re-run per drag frame and dominate drag cost at large works.
  function swapSrc(id: string) {
    const n = findNode(id)
    if (!n || n.type !== 'image') return
    const want = imgSrc(id, n.data)
    if (!want || shownSrc.value[id] === want) return
    if (!shownSrc.value[id] || want.startsWith('data:')) { shownSrc.value[id] = want; return } // no old to hold
    const pre = new Image()
    pre.onload = () => { const cur = findNode(id); if (cur && imgSrc(id, cur.data) === want) shownSrc.value[id] = want }
    pre.onerror = () => { /* keep the current (older) src on a failed derivative rather than blanking the card */ }
    pre.src = want
  }
  // When the supersample bucket steps (work grew/shrank past a threshold), re-target every image.
  watch(headroom, () => { for (const n of nodes.value) if (n.type === 'image') swapSrc(n.id) })

  // Set an image to a reference scale (long side = BASE_LONG × scale), keeping its aspect ratio. Aspect
  // ratio comes from a stable stored `ar`, never re-derived from the last rounded size (which would
  // accumulate rounding error and progressively crop under object-fit:cover).
  function applyScale(node: any, scale: number) {
    const live = findNode(node.id)
    if (!live) return
    if (live.data.ar == null) { const d0 = dims(live); live.data = { ...live.data, ar: d0.w / d0.h } }
    const ar = live.data.ar
    const long = BASE_LONG * scale
    const w = Math.round(ar >= 1 ? long : long * ar)
    const h = Math.round(ar >= 1 ? long / ar : long)
    live.style = { width: `${w}px`, height: `${h}px` }
    swapSrc(live.id) // scale changed the target ?w= → preload + swap without blanking
  }

  return { shownSrc, scaleOf, imgScale, fullStyle, imgSrc, seedSrc, swapSrc, applyScale }
}
