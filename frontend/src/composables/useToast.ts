import { ref } from 'vue'

export interface Toast {
  id: number
  text: string
  kind: 'ok' | 'err'
}

const toasts = ref<Toast[]>([])
let seq = 0

export function useToast() {
  function push(text: string, kind: 'ok' | 'err' = 'ok', ttl = 3500) {
    const id = ++seq
    toasts.value.push({ id, text, kind })
    setTimeout(() => { toasts.value = toasts.value.filter((t) => t.id !== id) }, ttl)
  }
  return { toasts, push }
}
