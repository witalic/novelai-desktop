// The few settings the shell needs, WITHOUT importing the Python pydantic config: real env vars
// first, then a minimal backend/.env scan (same precedence as pydantic-settings). Mirrors
// app/settings.py (NAI_ prefix, `__` nested delimiter).
const fs = require('fs')
const path = require('path')

const repoRoot = path.join(__dirname, '..')

function readEnvFile () {
  const out = {}
  try {
    const txt = fs.readFileSync(path.join(repoRoot, 'backend', '.env'), 'utf8')
    for (let line of txt.split(/\r?\n/)) {
      line = line.trim()
      if (!line || line.startsWith('#')) continue
      const eq = line.indexOf('=')
      if (eq < 0) continue
      const k = line.slice(0, eq).trim()
      let v = line.slice(eq + 1).trim()
      if (v.length >= 2 && (v[0] === '"' || v[0] === "'") && v[v.length - 1] === v[0]) v = v.slice(1, -1)
      out[k] = v
    }
  } catch { /* no backend/.env -> rely on defaults */ }
  return out
}

const envFile = readEnvFile()
function get (name, def) {
  if (process.env[name] !== undefined && process.env[name] !== '') return process.env[name]
  if (envFile[name] !== undefined && envFile[name] !== '') return envFile[name]
  return def
}

const host = get('NAI_API__HOST', '127.0.0.1')
// No hardcoded port: the shell picks a FREE loopback port per launch (see api.js) so it can never bind a
// port a foreign process holds, nor load that process's /app/. An explicit NAI_API__PORT is a dev override
// (and the only case where an already-running backend is reused).
const explicitPort = get('NAI_API__PORT', '') || null
const isLocal = ['127.0.0.1', 'localhost', '::1'].includes(host)
const baseUrl = (port) => `http://${host}:${port}`

module.exports = { repoRoot, host, explicitPort, isLocal, baseUrl }
