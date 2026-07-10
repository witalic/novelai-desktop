// novelai-desktop Electron shell — bring the sidecar up, open the window on the local web UI at
// /app/, and tear the sidecar down on quit. The window has no preload and no node integration; the
// web UI talks to the backend over plain HTTP on the single loopback origin.
const { app, BrowserWindow, session, dialog, Menu, ipcMain, shell } = require('electron')
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
  if (typeof target === 'string' && target) await shell.openPath(target)
  return true
})

function createWindow () {
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
  win.loadURL(`${cfg.clientBaseUrl}/app/`)

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
