// Backend sidecar lifecycle. ensureApi brings the FastAPI sidecar up on a free loopback port and returns
// { proc, baseUrl, origin } so main can load /app/ and lock navigation to that exact origin, and the quit
// handler can tear the sidecar down with the window.
const { spawn } = require('child_process')
const crypto = require('crypto')
const net = require('net')
const fs = require('fs')
const path = require('path')
const cfg = require('./config')

// Packaged builds spawn the frozen sidecar binary (no Python on the user's machine); dev spawns the venv.
let isPackaged = false
try { isPackaged = require('electron').app.isPackaged } catch { /* not under electron (unit tests) */ }

const newToken = () => crypto.randomBytes(32).toString('hex') // per-launch shared secret (cookie-delivered)
const sigOf = (token) => (token ? crypto.createHash('sha256').update(token).digest('hex') : null)

// Ask the OS for an ephemeral free port on the loopback host (bind :0, read it back, release it).
function freePort () {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.once('error', reject)
    srv.listen(0, cfg.host, () => {
      const { port } = srv.address()
      srv.close(() => resolve(port))
    })
  })
}

async function healthOk (baseUrl, expectedSig = null, timeoutMs = 1000) {
  try {
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), timeoutMs)
    const r = await fetch(`${baseUrl}/health`, { signal: ctrl.signal })
    clearTimeout(t)
    if (r.status !== 200) return false
    if (!expectedSig) return true
    // Confirm this is the backend WE spawned (its /health echoes sha256(token)), not a foreign process that
    // grabbed the free port between our probe and uvicorn's bind — otherwise it'd receive our auth cookie.
    const body = await r.json().catch(() => ({}))
    return body.sig === expectedSig
  } catch { return false }
}

// The venv interpreter — Windows (Scripts/python.exe) or POSIX (bin/python).
function pythonPath () {
  const winPy = path.join(cfg.repoRoot, '.venv', 'Scripts', 'python.exe')
  const nixPy = path.join(cfg.repoRoot, '.venv', 'bin', 'python')
  return fs.existsSync(winPy) ? winPy : nixPy
}

// The frozen sidecar (PyInstaller onedir), bundled as an Electron extraResource under resources/backend/.
function frozenBackend () {
  const exe = process.platform === 'win32' ? 'novelai-backend.exe' : 'novelai-backend'
  return path.join(process.resourcesPath, 'backend', 'novelai-backend', exe)
}

function spawnApi (port, token) {
  const baseUrl = cfg.baseUrl(port)
  const env = { ...process.env, NAI_API__HOST: cfg.host, NAI_API__PORT: String(port), NAI_API__AUTH_TOKEN: token }
  const proc = isPackaged
    ? spawn(frozenBackend(), [], { env, stdio: 'inherit' })                       // packaged: standalone binary
    : spawn(pythonPath(), ['-m', 'app'], { cwd: cfg.repoRoot, env, stdio: 'inherit' }) // dev: venv python
  return { proc, baseUrl, origin: new URL(baseUrl).origin, token }
}

async function waitHealthy (started, tries = 40) {
  const expected = sigOf(started.token) // verify identity, not just liveness
  for (let i = 0; i < tries; i++) {           // ~20s to boot
    if (await healthOk(started.baseUrl, expected)) return started
    if (started.proc && started.proc.exitCode !== null) break
    await new Promise((r) => setTimeout(r, 500))
  }
  if (started.proc) { try { started.proc.kill() } catch { /* already gone */ } }
  throw new Error(isPackaged ? 'Could not start the bundled backend sidecar.' : 'Could not start the local backend sidecar (is the .venv installed?).')
}

async function ensureApi () {
  // Dev override: an explicit port may already host a `python -m app` — reuse it (unguarded, no token),
  // else spawn our own there with a fresh secret.
  if (cfg.explicitPort) {
    const baseUrl = cfg.baseUrl(cfg.explicitPort)
    if (await healthOk(baseUrl)) return { proc: null, baseUrl, origin: new URL(baseUrl).origin, token: '' }
    return waitHealthy(spawnApi(cfg.explicitPort, newToken()))
  }
  if (!cfg.isLocal) throw new Error(`Cannot start a backend on non-loopback host ${cfg.host}.`)
  return waitHealthy(spawnApi(await freePort(), newToken()))
}

module.exports = { healthOk, ensureApi }
