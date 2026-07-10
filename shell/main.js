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

function createWindow (apiOrigin) {
  Menu.setApplicationMenu(null)
  win = new BrowserWindow({
    width: 1280,
    height: 860,
    title: 'NovelAI Desktop',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  })
  // Lock the window to the sidecar origin: any off-origin navigation is refused and external links open in
  // the OS browser — the preload bridge (folder picker, openPath, quit hook) must never reach remote content.
  win.webContents.on('will-navigate', (e, url) => {
    if (new URL(url).origin !== apiOrigin) e.preventDefault()
  })
  win.webContents.setWindowOpenHandler(({ url }) => {
    try { if (/^https?:/.test(url)) shell.openExternal(url) } catch { /* ignore */ }
    return { action: 'deny' }
  })
  win.loadURL(`${apiOrigin}/app/`)

  // Give the renderer a chance to flush unsaved work before the window closes.
  let quitting = false
  win.on('close', (e) => {
    if (quitting) return
    e.preventDefault()
    let done = false
    const finish = () => { if (done) return; done = true; quitting = true; win.destroy() }
    ipcMain.once('app:quit-ready', finish)
    win.webContents.send('app:before-quit')
    setTimeout(finish, 3000) // safety: never hang the close on a stuck renderer
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
