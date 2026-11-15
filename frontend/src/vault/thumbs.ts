/* Thumbnails are sized CLOSE to the tile they render into — deliberately NOT oversized. When many tiles decode at
 * once, Chromium downsamples decoded bitmaps under its memory budget → pixelation (ui-design.md: one image in the
 * previewer stays sharp, a full grid crushes). The escape is a small source: a thumbnail near the display size leaves
 * Chromium nothing to sub-sample. So we target ~1.5× the physical tile width (enough to stay crisp, small enough that
 * 30+ tiles fit the budget) and snap to a bucket for cache reuse. Going bigger (a fat oversample) reads sharper for ONE
 * tile but reintroduces the crush across a grid. A `data:` URL can't be server-resized, so it's used as-is. */

const OVERSAMPLE = 1.5 // physical-tile → thumbnail-width factor: crisp but memory-light
const BUCKETS = [128, 192, 256, 320, 384, 512, 640, 768] // past the top the original wins (few big tiles = no crush)

export const DPR = Math.min(typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1, 2)

/** A vault image's URL sized for a tile `tilePx` CSS pixels wide. */
export function thumbSrc(url: string | undefined, tilePx: number, dpr: number = DPR): string {
  if (!url || url.startsWith('data:')) return url || ''
  const target = Math.ceil(tilePx * dpr * OVERSAMPLE)
  for (const b of BUCKETS) if (b >= target) return `${url}?w=${b}`
  return url // tile larger than any bucket → the full-resolution original is the sharpest option
}
