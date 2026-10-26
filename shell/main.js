// novelai-desktop Electron shell — bring the sidecar up on a free loopback port, open the window on the
// local web UI at /app/, and tear the sidecar down on quit. The window uses a minimal contextBridge
// preload (no node integration); the web UI talks to the backend over plain HTTP on the single origin.
const { app, BrowserWindow, session, dialog, Menu, ipcMain, shell } = require('electron')
const fs = require('fs')
const path = require('path')
const cfg = require('./config')
const { ensureApi } = require('./api')

let apiProc = null
let win = null

// Native folder picker + open-in-file-manager for the Settings vault manager (see preload.js).
ipcMain.handle('dialog:pickFolder', async () => {
  const res = await dialog.showOpenDialog(win, { properties: ['openDirectory', 'createDirectory'] })
  return (res.canceled || !res.filePaths.length) ? null : res.filePaths[0]
})
ipcMain.handle('shell:openPath', async (_e, target) => {
  // Only ever open an existing DIRECTORY (the vault folder) — never a file/executable the renderer names.
  try {
    if (typeof target === 'string' && target && fs.statSync(target).isDirectory()) {
      await shell.openPath(target)
      return true
    }
  } catch { /* missing / not a directory */ }
  return false
})
// The app draws its own title bar (titleBarStyle 'hidden'), so its window buttons come through here. No
// arguments — the renderer can only minimize / maximize-or-restore / close its own window.
const ownWindow = (e) => (win && e.sender === win.webContents ? win : null)
ipcMain.handle('window:minimize', (e) => { ownWindow(e)?.minimize() })
ipcMain.handle('window:toggle-maximize', (e) => {
  const w = ownWindow(e)
  if (w) w.isMaximized() ? w.unmaximize() : w.maximize()
})
ipcMain.handle('window:close', (e) => { ownWindow(e)?.close() }) // → the flush-before-quit handshake below
ipcMain.handle('window:is-maximized', (e) => !!ownWindow(e)?.isMaximized())

function createWindow (apiOrigin) {
  Menu.setApplicationMenu(null)
  win = new BrowserWindow({
    width: 1280,
    height: 860,
    title: 'NovelAI Desktop',
    // No OS title bar or buttons: the UI draws its own app bar (brand + window buttons, a drag region).
    // Edge-resize and snapping stay native.
    titleBarStyle: 'hidden',
    backgroundColor: '#101214',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  })
  // Lock the window to the sidecar origin: any off-origin navigation (top frame, subframes, redirects) is
  // refused and external links open in the OS browser — the preload bridge (folder picker, openPath, quit
  // hook) must never reach remote content. `new URL` is wrapped so a malformed url can't throw past the guard.
  const sameOrigin = (url) => { try { return new URL(url).origin === apiOrigin } catch { return false } }
  win.webContents.on('will-navigate', (e, url) => { if (!sameOrigin(url)) e.preventDefault() })
  win.webContents.on('will-redirect', (e, url) => { if (!sameOrigin(url)) e.preventDefault() })
  win.webContents.on('will-frame-navigate', (e) => { if (!sameOrigin(e.url)) e.preventDefault() })
  win.webContents.setWindowOpenHandler(({ url }) => {
    try { if (/^https?:/.test(url)) shell.openExternal(url) } catch { /* ignore */ }
    return { action: 'deny' }
  })
  win.loadURL(`${apiOrigin}/app/`)
  // Keep the app bar's maximize/restore icon in step with the window (snap, double-click, Win+↑ …).
  const sendMaximized = () => win.webContents.send('window:maximized', win.isMaximized())
  win.on('maximize', sendMaximized)
  win.on('unmaximize', sendMaximized)

  // Give the renderer a chance to flush unsaved work before the window closes — and, when that fails, to ask the
  // user (it may keep the window: quit-cancel). While it asks (quit-hold) the stuck-renderer timeout is off.
  let quitting = false
  let asking = false
  win.on('close', (e) => {
    if (quitting) return
    e.preventDefault()
    if (asking) return // closed again while the renderer is still deciding
    asking = true
    const onReady = () => settle(true)
    const onCancel = () => settle(false)
    const onHold = () => clearTimeout(timer)
    const settle = (quit) => {
      if (!asking) return
      asking = false
      clearTimeout(timer)
      ipcMain.removeListener('app:quit-ready', onReady)
      ipcMain.removeListener('app:quit-cancel', onCancel)
      ipcMain.removeListener('app:quit-hold', onHold)
      if (quit) { quitting = true; win.destroy() }
    }
    ipcMain.on('app:quit-ready', onReady)
    ipcMain.on('app:quit-cancel', onCancel)
    ipcMain.on('app:quit-hold', onHold)
    const timer = setTimeout(() => settle(true), 10000) // a stuck renderer — generous so a large real save can finish
    win.webContents.send('app:before-quit')
  })
}

app.whenReady().then(async () => {
  console.log(`[shell] electron=${process.versions.electron} chromium=${process.versions.chrome}`)
  let api
  try {
    api = await ensureApi()
  } catch (e) {
    dialog.showErrorBox('NovelAI Desktop', String((e && e.message) || e))
    app.quit()
    return
  }
  apiProc = api.proc
  console.log(`[shell] api=${api.baseUrl}`)
  // The shell loads its OWN local web UI — never serve a stale cached bundle during dev.
  await session.defaultSession.clearCache()
  // Cookie-delivered per-launch secret (SameSite=Strict → sent on same-origin fetch AND <img> loads, never
  // cross-site). The backend /api guard checks it; set it BEFORE loading /app/.
  if (api.token) {
    try {
      await session.defaultSession.cookies.set({
        url: api.baseUrl, name: 'nai_auth', value: api.token, sameSite: 'strict', httpOnly: true,
      })
    } catch (e) { console.error('[shell] cookie set failed', e) }
  }
  // CSP on every response: the UI only loads its own bundle + data:/blob: images, so an injected external
  // script/frame can't escalate a stored string into control of the (loopback) API.
  session.defaultSession.webRequest.onHeadersReceived((details, cb) => {
    cb({ responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': [
      "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; " +
      "font-src 'self' data:; connect-src 'self' ws://127.0.0.1:* wss://127.0.0.1:*; object-src 'none'; base-uri 'self'",
    ] } })
  })
  // The local UI needs no device permissions (camera, geolocation, notifications, …) — deny them all.
  session.defaultSession.setPermissionRequestHandler((_wc, _perm, cb) => cb(false))
  createWindow(api.origin)
})

// Tear the sidecar down on normal quit AND on crash/signal, so a spawned python is never orphaned holding
// its port. (Kept out of before-quit so the close-time renderer flush still reaches a live backend.)
function killApi () { if (apiProc) { try { apiProc.kill() } catch { /* gone */ } apiProc = null } }
app.on('window-all-closed', () => app.quit())
app.on('quit', killApi)
process.on('exit', killApi)
for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(sig, () => { killApi(); process.exit(0) })
process.on('uncaughtException', (err) => { console.error('[shell] uncaught', err); killApi(); process.exit(1) })
