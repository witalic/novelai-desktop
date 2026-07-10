import { ref, watch, watchEffect } from 'vue'
import { getAppSettings, patchAppSettings } from '../api'

export type Theme = 'light' | 'dark'
export interface Accent { name: string; a: string; s: string }

export const ACCENTS: Accent[] = [
  { name: 'Blue', a: '#0c66e4', s: '#0055cc' },
  { name: 'Green', a: '#1f845a', s: '#166b48' },
  { name: 'Purple', a: '#6e5dc6', s: '#5b4db0' },
  { name: 'Magenta', a: '#ae4787', s: '#953a72' },
  { name: 'Orange', a: '#b65c02', s: '#974c02' },
]

// localStorage is only a flash-free cache; the OS-config-dir settings.json is the source of truth.
const THEME_KEY = 'nai.theme'
const ACCENT_KEY = 'nai.accent'

const theme = ref<Theme>((localStorage.getItem(THEME_KEY) as Theme) || 'dark')
const accent = ref<string>(localStorage.getItem(ACCENT_KEY) || ACCENTS[0].a)
let suppressSave = false

// Apply to <html> so tokens.css [data-theme] + --accent take effect app-wide, and cache for next launch.
watchEffect(() => {
  const root = document.documentElement
  root.dataset.theme = theme.value
  localStorage.setItem(THEME_KEY, theme.value)
  const acc = ACCENTS.find((x) => x.a === accent.value) ?? ACCENTS[0]
  root.style.setProperty('--accent', acc.a)
  root.style.setProperty('--accent-strong', acc.s)
  localStorage.setItem(ACCENT_KEY, accent.value)
})

// Load the persisted prefs once; then persist user changes back to the backend (synchronously-flushed
// watch so the load below doesn't echo a save).
getAppSettings().then((s) => {
  suppressSave = true
  if (s.theme === 'light' || s.theme === 'dark') theme.value = s.theme
  if (s.accent) accent.value = s.accent
  suppressSave = false
}).catch(() => { /* backend not up yet — the cached values stand in */ })

watch([theme, accent], () => {
  if (suppressSave) return
  patchAppSettings({ theme: theme.value, accent: accent.value }).catch(() => { /* best-effort */ })
}, { flush: 'sync' })

export function useTheme() {
  return { theme, accent, accents: ACCENTS }
}
