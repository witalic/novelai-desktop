/// <reference types="vite/client" />

// Injected by Vite `define` from frontend/package.json — the single source of truth for the app version.
declare const __APP_VERSION__: string

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}
