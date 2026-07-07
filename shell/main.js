// novelai-desktop Electron shell — bring the sidecar up, open the window on the local web UI at
// /app/, and tear the sidecar down on quit. The window has no preload and no node integration; the
// web UI talks to the backend over plain HTTP on the single loopback origin.
const { app, BrowserWindow, session, dialog, Menu } = require('electron')
const cfg = require('./config')
const { ensureApi } = require('./api')

let apiProc = null
let win = null

function createWindow () {
  Menu.setApplicationMenu(null)
  win = new BrowserWindow({
    width: 1280,
    height: 860,
    title: 'NovelAI Desktop',
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  })
  win.loadURL(`${cfg.clientBaseUrl}/app/`)
}

app.whenReady().then(async () => {
  console.log(`[shell] electron=${process.versions.electron} chromium=${process.versions.chrome}`)
  console.log(`[shell] api=${cfg.clientBaseUrl}`)
  try {
    apiProc = await ensureApi()
  } catch (e) {
    dialog.showErrorBox('NovelAI Desktop', String((e && e.message) || e))
    app.quit()
    return
  }
  // The shell loads its OWN local web UI — never serve a stale cached bundle during dev.
  await session.defaultSession.clearCache()
  createWindow()
})

app.on('window-all-closed', () => app.quit())
app.on('quit', () => { if (apiProc) { try { apiProc.kill() } catch { /* gone */ } } })
