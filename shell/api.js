// Backend sidecar lifecycle. ensureApi brings the FastAPI sidecar up on a free loopback port and returns
// { proc, baseUrl, origin } so main can load /app/ and lock navigation to that exact origin, and the quit
// handler can tear the sidecar down with the window.
const { spawn } = require('child_process')
const net = require('net')
const fs = require('fs')
const path = require('path')
const cfg = require('./config')

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

async function healthOk (baseUrl, timeoutMs = 1000) {
  try {
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), timeoutMs)
    const r = await fetch(`${baseUrl}/health`, { signal: ctrl.signal })
    clearTimeout(t)
    return r.status === 200
  } catch { return false }
}

// The venv interpreter — Windows (Scripts/python.exe) or POSIX (bin/python).
function pythonPath () {
  const winPy = path.join(cfg.repoRoot, '.venv', 'Scripts', 'python.exe')
  const nixPy = path.join(cfg.repoRoot, '.venv', 'bin', 'python')
  return fs.existsSync(winPy) ? winPy : nixPy
}

function spawnApi (port) {
  const baseUrl = cfg.baseUrl(port)
  const py = pythonPath()
  const env = { ...process.env, NAI_API__HOST: cfg.host, NAI_API__PORT: String(port) }
  const proc = spawn(py, ['-m', 'app'], { cwd: cfg.repoRoot, env, stdio: 'inherit' })
  return { proc, baseUrl, origin: new URL(baseUrl).origin }
}

async function waitHealthy (started, tries = 40) {
  for (let i = 0; i < tries; i++) {           // ~20s to boot
    if (await healthOk(started.baseUrl)) return started
    if (started.proc && started.proc.exitCode !== null) break
    await new Promise((r) => setTimeout(r, 500))
  }
  if (started.proc) { try { started.proc.kill() } catch { /* already gone */ } }
  throw new Error('Could not start the local backend sidecar (is the .venv installed?).')
}

async function ensureApi () {
  // Dev override: an explicit port may already host a `python -m app` — reuse it, else spawn there.
  if (cfg.explicitPort) {
    const baseUrl = cfg.baseUrl(cfg.explicitPort)
    if (await healthOk(baseUrl)) return { proc: null, baseUrl, origin: new URL(baseUrl).origin }
    return waitHealthy(spawnApi(cfg.explicitPort))
  }
  if (!cfg.isLocal) throw new Error(`Cannot start a backend on non-loopback host ${cfg.host}.`)
  return waitHealthy(spawnApi(await freePort()))
}

module.exports = { healthOk, ensureApi }
