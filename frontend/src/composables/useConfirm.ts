import { ref } from 'vue'

export interface ConfirmOptions {
  title?: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
}

interface ConfirmState extends Required<Omit<ConfirmOptions, 'title'>> {
  title: string
  open: boolean
}

const state = ref<ConfirmState>({
  open: false, title: '', message: '', confirmLabel: 'Confirm', cancelLabel: 'Cancel', danger: false,
})
let resolver: ((ok: boolean) => void) | null = null

// App-styled confirmation dialog (replaces window.confirm). One instance, driven like useToast.
export function useConfirm() {
  function confirm(opts: ConfirmOptions): Promise<boolean> {
    resolver?.(false) // a still-pending confirm is superseded → resolve it as cancelled, don't leave it hanging
    state.value = {
      open: true, title: opts.title ?? 'Are you sure?', message: opts.message,
      confirmLabel: opts.confirmLabel ?? 'Confirm', cancelLabel: opts.cancelLabel ?? 'Cancel',
      danger: opts.danger ?? false,
    }
    return new Promise((res) => { resolver = res })
  }
  function settle(ok: boolean) {
    state.value.open = false
    resolver?.(ok)
    resolver = null
  }
  return { state, confirm, settle }
}
