// Thin wrapper over the Electron preload bridge (window.electronAPI). In a plain browser (dev/preview)
// there is no bridge, so folder-pick falls back to a path prompt and open-in-explorer is a no-op.
interface ElectronAPI {
  pickFolder(): Promise<string | null>
  openPath(target: string): Promise<boolean>
  onBeforeQuit(handler: () => Promise<void> | void): () => void
}
declare global {
  interface Window { electronAPI?: ElectronAPI }
}

// Register a flush-on-close handler (Electron only). Main waits for it before the app exits.
// Returns a disposer to unregister on unmount (no-op outside Electron) so listeners never stack.
export function onBeforeQuit(handler: () => Promise<void> | void): () => void {
  return window.electronAPI?.onBeforeQuit(handler) ?? (() => {})
}

export async function browseFolder(promptText = 'Enter a folder path'): Promise<string | null> {
  if (window.electronAPI) return window.electronAPI.pickFolder()
  const entered = window.prompt(promptText)
  return entered && entered.trim() ? entered.trim() : null
}

export async function openInFileManager(target: string): Promise<void> {
  if (window.electronAPI) await window.electronAPI.openPath(target)
}
