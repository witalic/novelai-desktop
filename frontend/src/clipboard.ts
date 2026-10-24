// Copy text to the clipboard. The async Clipboard API needs a permission the Electron shell denies (it
// refuses every permission request), so fall back to a one-shot hidden textarea + execCommand('copy'),
// which only needs the user's click.
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch { /* no permission / not a secure context → fallback */ }
  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  try {
    return document.execCommand('copy')
  } finally {
    ta.remove()
  }
}
