import type { GenerateParams, GenerateResponse } from './types'

// Same-origin in production (served at /app/ by FastAPI); proxied to the backend in Vite dev.
export async function generate(params: GenerateParams): Promise<GenerateResponse> {
  const resp = await fetch('/api/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!resp.ok) {
    let detail = `Request failed (HTTP ${resp.status})`
    try {
      const body = await resp.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* non-JSON error body */ }
    throw new Error(detail)
  }
  return resp.json()
}
