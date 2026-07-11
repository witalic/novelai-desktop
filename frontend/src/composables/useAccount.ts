/* Shared NovelAI account state (subscription tier + Anlas balance). Singleton module state so the
 * sidebar, the generate button, and preset cards read the same balance; refresh() coalesces. */
import { ref } from 'vue'
import { getSubscription } from '../api'
import type { Subscription } from '../types'

const subscription = ref<Subscription | null>(null)
let inflight: Promise<unknown> | null = null

export function useAccount() {
  function refresh() {
    if (!inflight) {
      inflight = getSubscription()
        .then((s) => { subscription.value = s })
        .catch(() => { /* no token / offline — leave the last known balance */ })
        .finally(() => { inflight = null })
    }
    return inflight
  }
  return { subscription, refresh }
}
