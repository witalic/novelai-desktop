// Thin wrapper over the Electron preload bridge (window.electronAPI). In a plain browser (dev/preview)
// there is no bridge, so folder-pick falls back to a path prompt, open-in-explorer is a no-op, and there
// are no window controls (the browser has its own).
export interface WindowControls {
  minimize(): Promise<void>
  toggleMaximize(): Promise<void>
  close(): Promise<void>
  isMaximized(): Promise<boolean>
  onMaximizedChange(handler: (maximized: boolean) => void): () => void
}
interface ElectronAPI {
  pickFolder(): Promise<string | null>
  openPath(target: string): Promise<boolean>
  onBeforeQuit(handler: QuitHandler): () => void
  windowControls: WindowControls
}
declare global {
  interface Window { electronAPI?: ElectronAPI }
}

// A flush-on-close handler: true (or nothing) lets the app quit, false keeps the window open. It calls `hold()` before
// asking the user, so main's stuck-renderer timeout doesn't close the window under the question.
export type QuitHandler = (hold: () => void) => Promise<boolean | void> | boolean | void

// Register a flush-on-close handler (Electron only). Main waits for it before the app exits.
// Returns a disposer to unregister on unmount (no-op outside Electron) so listeners never stack.
export function onBeforeQuit(handler: QuitHandler): () => void {
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

// The app bar's minimize / maximize / close — null outside Electron.
export const windowControls = (): WindowControls | null => window.electronAPI?.windowControls ?? null
