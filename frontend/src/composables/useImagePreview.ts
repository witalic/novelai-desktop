import { ref } from 'vue'

/* A single full-screen image preview (lightbox), driven like useConfirm/useToast: one instance mounted
 * in App.vue, opened from anywhere via preview(url). */
const src = ref<string | null>(null)

export function useImagePreview() {
  function preview(url: string) { if (url) src.value = url }
  function close() { src.value = null }
  return { src, preview, close }
}
