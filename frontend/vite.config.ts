import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Built to frontend/dist and served single-origin by FastAPI at /app/ (see README).
// In dev, Vite proxies API calls to the backend sidecar so the app stays same-origin.
export default defineConfig({
  base: '/app/',
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8787',
      '/health': 'http://127.0.0.1:8787',
    },
  },
})
