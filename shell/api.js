// Backend sidecar lifecycle. ensureApi brings the FastAPI sidecar up (reusing one already running,
// e.g. a dev server) and returns the process so the quit handler can tear it down with the window.
const { spawn } = require('child_process')
const path = require('path')
const cfg = require('./config')

async function healthOk (timeoutMs = 1000) {
  try {
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), timeoutMs)
    const r = await fetch(`${cfg.clientBaseUrl}/health`, { signal: ctrl.signal })
    clearTimeout(t)
    return r.status === 200
  } catch { return false }
}

async function ensureApi () {
  if (await healthOk()) return null   // already running (e.g. `python -m app` in another window) — reuse
  if (!cfg.isLocal) throw new Error(`Cannot reach the backend at ${cfg.clientBaseUrl}.`)

  const py = path.join(cfg.repoRoot, '.venv', 'Scripts', 'python.exe')
  const env = { ...process.env, NAI_API__HOST: cfg.host, NAI_API__PORT: String(cfg.port) }
  const proc = spawn(py, ['-m', 'app'], { cwd: cfg.repoRoot, env, stdio: 'inherit' })

  for (let i = 0; i < 40; i++) {           // ~20s to boot
    if (await healthOk()) return proc
    if (proc.exitCode !== null) break
    await new Promise((r) => setTimeout(r, 500))
  }
  try { proc.kill() } catch { /* already gone */ }
  throw new Error('Could not start the local backend sidecar (is the .venv installed?).')
}

module.exports = { healthOk, ensureApi }
