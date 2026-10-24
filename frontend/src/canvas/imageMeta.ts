// The image viewer's one-line description of an image: size · seed · when (e.g. "832×1216 · seed 2840193 ·
// 2 min ago"). Parts that are unknown are left out.
export function imageMeta(params: { width?: number; height?: number; seed?: number | null } | undefined, createdAt?: string,
  now: number = Date.now()): string {
  const parts: string[] = []
  if (params?.width && params?.height) parts.push(`${params.width}×${params.height}`)
  if (params?.seed != null) parts.push(`seed ${params.seed}`)
  const when = createdAt ? ago(Date.parse(createdAt), now) : ''
  if (when) parts.push(when)
  return parts.join(' · ')
}

function ago(t: number, now: number): string {
  if (Number.isNaN(t)) return ''
  const min = Math.floor((now - t) / 60_000)
  if (min < 1) return 'just now'
  if (min < 60) return `${min} min ago`
  if (min < 24 * 60) return `${Math.floor(min / 60)} h ago`
  return new Date(t).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })
}
