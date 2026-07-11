import { ref } from 'vue'

/* One app-wide right-click menu, driven like useConfirm: a component reads this state, callers open it
 * with a screen position + a list of items. Kept generic so the stack, the station output, and the
 * canvas all share one implementation and one look. */
export interface MenuItem {
  label: string
  icon?: string
  danger?: boolean
  onClick: () => void
}

interface MenuState { open: boolean; x: number; y: number; items: MenuItem[] }

const state = ref<MenuState>({ open: false, x: 0, y: 0, items: [] })

export function useContextMenu() {
  function open(e: MouseEvent, items: MenuItem[]) {
    if (!items.length) return
    e.preventDefault()
    state.value = { open: true, x: e.clientX, y: e.clientY, items }
  }
  function close() { state.value.open = false }
  function run(item: MenuItem) { close(); item.onClick() }
  return { state, open, close, run }
}
