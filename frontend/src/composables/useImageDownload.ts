/* Save image URLs to the Downloads folder via the backend (no OS dialog). Shared by the canvas and the
 * Works editor so the fetch+re-encode+toast pipeline lives in one place. Vault images serve as URLs (not
 * data: URIs), so they are fetched and re-encoded to raw base64 before /api/download writes them. */
import { saveDownloads } from '../api'
import { useToast } from './useToast'

async function toBase64(url: string): Promise<string> {
  if (url.startsWith('data:')) return url.slice(url.indexOf(',') + 1)
  const bytes = new Uint8Array(await (await fetch(url)).arrayBuffer())
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(bin)
}

export function useImageDownload() {
  const { push } = useToast()
  async function downloadUrls(urls: string[]): Promise<void> {
    if (!urls.length) return
    try {
      await saveDownloads(await Promise.all(urls.map(toBase64)))
      push(`Saved ${urls.length} image${urls.length === 1 ? '' : 's'} to Downloads`, 'ok')
    } catch (e) {
      push(e instanceof Error ? e.message : 'Download failed', 'err')
    }
  }
  return { downloadUrls }
}
