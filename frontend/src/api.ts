import type { GenerateParams, GenerateResponse, StreamEvent } from './types'

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

export interface VaultConfig {
  vault_dir: string | null
  initialized: boolean
  writable: boolean
  proposed_default: string
}

export async function getVaultConfig(): Promise<VaultConfig> {
  const resp = await fetch('/api/vault/config')
  if (!resp.ok) throw new Error(`Failed to read vault config (HTTP ${resp.status})`)
  return resp.json()
}

export async function setVaultConfig(vaultDir: string): Promise<VaultConfig> {
  const resp = await fetch('/api/vault/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ vault_dir: vaultDir }),
  })
  if (!resp.ok) {
    let detail = `Failed to set vault (HTTP ${resp.status})`
    try { const b = await resp.json(); if (b?.detail) detail = b.detail } catch { /* non-JSON */ }
    throw new Error(detail)
  }
  return resp.json()
}

// Save image(s) to the Downloads folder via the backend (no OS save dialog). `images` are raw base64.
export async function saveDownloads(images: string[]): Promise<{ count: number; dir: string }> {
  const resp = await fetch('/api/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ images }),
  })
  if (!resp.ok) {
    let detail = `Download failed (HTTP ${resp.status})`
    try { const b = await resp.json(); if (b?.detail) detail = b.detail } catch { /* non-JSON */ }
    throw new Error(detail)
  }
  return resp.json()
}

// POST-stream: reads the SSE response incrementally, invoking onEvent per event.
export async function generateStream(
  params: GenerateParams,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch('/api/generate/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
    signal,
  })
  if (!resp.ok || !resp.body) throw new Error(`Stream failed (HTTP ${resp.status})`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      const dataLine = frame.split('\n').find((l) => l.startsWith('data:'))
      if (dataLine) onEvent(JSON.parse(dataLine.slice(5).trim()))
    }
  }
}
