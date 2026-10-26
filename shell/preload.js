// Minimal, safe bridge for the renderer: a native folder picker and "open in file manager" (the Settings
// vault manager), the flush-before-quit hook, and the app bar's window buttons. contextIsolation is on, so
// only these calls are exposed.
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  pickFolder: () => ipcRenderer.invoke('dialog:pickFolder'),
  openPath: (target) => ipcRenderer.invoke('shell:openPath', target),
  // On window close, main asks the renderer to flush unsaved work before the app exits. The handler answers
  // whether to go on: false keeps the window (what couldn't be saved stays), and it calls `hold()` before
  // asking the user, so main stops its stuck-renderer timeout. Returns a disposer so the caller can
  // unregister on unmount — otherwise re-registrations stack listeners.
  onBeforeQuit: (handler) => {
    const listener = async () => {
      let quit = true
      try { quit = (await handler(() => ipcRenderer.send('app:quit-hold'))) !== false } catch { /* quit anyway */ }
      ipcRenderer.send(quit ? 'app:quit-ready' : 'app:quit-cancel')
    }
    ipcRenderer.on('app:before-quit', listener)
    return () => ipcRenderer.removeListener('app:before-quit', listener)
  },
  // The window has no OS title bar (titleBarStyle 'hidden'); the app bar drives it through these.
  windowControls: {
    minimize: () => ipcRenderer.invoke('window:minimize'),
    toggleMaximize: () => ipcRenderer.invoke('window:toggle-maximize'),
    close: () => ipcRenderer.invoke('window:close'),
    isMaximized: () => ipcRenderer.invoke('window:is-maximized'),
    onMaximizedChange: (handler) => {
      const listener = (_e, maximized) => handler(!!maximized)
      ipcRenderer.on('window:maximized', listener)
      return () => ipcRenderer.removeListener('window:maximized', listener)
    },
  },
})
