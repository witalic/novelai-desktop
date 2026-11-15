import { ref } from 'vue'

export interface ConfirmOptions {
  title?: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  // An option the confirm carries along (e.g. "Also its settings") — `ask` returns how it was left.
  check?: { label: string; hint?: string; checked: boolean }
}

interface ConfirmState extends Required<Omit<ConfirmOptions, 'title' | 'check'>> {
  title: string
  open: boolean
  check: ConfirmOptions['check'] | null
}

const state = ref<ConfirmState>({
  open: false, title: '', message: '', confirmLabel: 'Confirm', cancelLabel: 'Cancel', danger: false, check: null,
})
let resolver: ((r: { ok: boolean; checked: boolean }) => void) | null = null

// App-styled confirmation dialog (replaces window.confirm). One instance, driven like useToast.
export function useConfirm() {
  function ask(opts: ConfirmOptions): Promise<{ ok: boolean; checked: boolean }> {
    resolver?.({ ok: false, checked: false }) // a still-pending confirm is superseded → cancelled, not left hanging
    state.value = {
      open: true, title: opts.title ?? 'Are you sure?', message: opts.message,
      confirmLabel: opts.confirmLabel ?? 'Confirm', cancelLabel: opts.cancelLabel ?? 'Cancel',
      danger: opts.danger ?? false, check: opts.check ? { ...opts.check } : null,
    }
    return new Promise((res) => { resolver = res })
  }
  function confirm(opts: ConfirmOptions): Promise<boolean> {
    return ask(opts).then((r) => r.ok)
  }
  function settle(ok: boolean) {
    state.value.open = false
    resolver?.({ ok, checked: !!state.value.check?.checked })
    resolver = null
  }
  return { state, ask, confirm, settle }
}
