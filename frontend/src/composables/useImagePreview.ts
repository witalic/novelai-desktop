import { ref, shallowRef } from 'vue'

/* The full-screen image viewer (design/ui-polish.html §1–3), driven like useConfirm/useToast: one instance
 * mounted in App.vue (ImagePreview.vue), opened from anywhere. It pages through the SET it was opened from —
 * the stack, Quick access, one gallery grid — and shows that set's actions. `items` is a getter over live
 * state, so an action that takes the image out of the set (kept, removed, deleted) moves the viewer on to
 * the next one; an emptied set closes it. */
export interface ViewerItem {
  id: string
  url: string
  meta?: string // e.g. "832×1216 · seed 2840193 · 2 min ago"
  favorite?: boolean
}
export interface ViewerAction {
  label: string | ((item: ViewerItem) => string)
  icon: string
  key?: string // keyboard shortcut (KeyboardEvent.key, case-insensitive)
  primary?: boolean
  danger?: boolean
  // Close the viewer first — the action leads elsewhere (restores a recipe to the station) or opens a dialog
  // that must own the keyboard.
  closes?: boolean
  enabled?: (item: ViewerItem) => boolean // hidden for items it can't apply to (e.g. no recipe)
  run: (item: ViewerItem) => void | Promise<void>
}
export interface ViewerSet {
  source: string // shown as the set's name: "Stack", "Quick access", a grid's title
  items: () => ViewerItem[]
  actions: ViewerAction[]
}

const set = shallowRef<ViewerSet | null>(null)
const currentId = ref<string | null>(null)

export function useImagePreview() {
  function openViewer(s: ViewerSet, startId: string) {
    set.value = s
    currentId.value = startId
  }
  // One image with nothing to page through or act on (e.g. a loose canvas image).
  function preview(url: string) {
    if (url) openViewer({ source: 'Image', items: () => [{ id: url, url }], actions: [] }, url)
  }
  function close() {
    set.value = null
    currentId.value = null
  }
  const isOpen = () => set.value !== null
  return { set, currentId, openViewer, preview, close, isOpen }
}
