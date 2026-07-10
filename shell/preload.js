// Minimal, safe bridge for the renderer: a native folder picker and "open in file manager", used
// by the Settings vault manager. contextIsolation is on, so only these two calls are exposed.
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  pickFolder: () => ipcRenderer.invoke('dialog:pickFolder'),
  openPath: (target) => ipcRenderer.invoke('shell:openPath', target),
  // On window close, main asks the renderer to flush unsaved work before the app exits. Returns a
  // disposer so the caller can unregister on unmount — otherwise re-registrations stack listeners.
  onBeforeQuit: (handler) => {
    const listener = async () => {
      try { await handler() } finally { ipcRenderer.send('app:quit-ready') }
    }
    ipcRenderer.on('app:before-quit', listener)
    return () => ipcRenderer.removeListener('app:before-quit', listener)
  },
})
