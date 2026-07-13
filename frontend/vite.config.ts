import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import pkg from './package.json'

// Built to frontend/dist and served single-origin by FastAPI at /app/ (see README).
// In dev, Vite proxies API calls to the backend sidecar so the app stays same-origin.
export default defineConfig({
  base: '/app/',
  // Single source of truth for the app version — frontend/package.json (keep shell/package.json in sync).
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8787',
      '/health': 'http://127.0.0.1:8787',
    },
  },
  // Unit tests (vitest). serialize.ts is framework-free by design, so a plain node env is enough.
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
