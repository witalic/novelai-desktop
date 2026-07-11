/* Downscale a fresh data: URL to a small thumbnail data: URL (client-side, one canvas draw).
 *
 * The stack can hold up to 50 freshly generated images as full-resolution data: URLs. Rendering that
 * many large sources makes Chromium downsample decoded bitmaps under its decode-memory budget → the
 * thumbnails look pixelated (the vault path avoids this by fetching `?w=` server thumbnails). Shrinking
 * each source once keeps the displayed bitmaps small, so the stack stays crisp like the vault path. */
export function downscaleDataUrl(url: string, maxLong: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      const long = Math.max(img.naturalWidth, img.naturalHeight)
      const scale = long > maxLong ? maxLong / long : 1
      const w = Math.max(1, Math.round(img.naturalWidth * scale))
      const h = Math.max(1, Math.round(img.naturalHeight * scale))
      const canvas = document.createElement('canvas')
      canvas.width = w
      canvas.height = h
      const ctx = canvas.getContext('2d')
      if (!ctx) { resolve(url); return } // no 2D context — fall back to the source (rare)
      ctx.drawImage(img, 0, 0, w, h)
      resolve(canvas.toDataURL('image/jpeg', 0.86))
    }
    img.onerror = reject
    img.src = url
  })
}
