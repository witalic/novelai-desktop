/* Real token counts for the generation usage indicator. The prompt recomposes on every keystroke, so
 * the backend /api/tokenize call is DEBOUNCED (~300ms, like autosave — not per-keystroke) and
 * STALE-GUARDED (a monotonic id drops an out-of-order response, mirroring GenerateView's genToken).
 * Before the first real count lands, a local ~4-chars/token heuristic fills in so the indicator never
 * blanks; once a real count arrives it holds through the debounce window (no flicker). Offline / backend
 * down keeps the last known count. `tokenize` is injectable for tests (defaults to the real API call). */
import { onScopeDispose, ref, watch } from 'vue'
import { tokenize as apiTokenize } from '../api'
import type { TokenizeRequest, TokenizeResponse } from '../types'

type Source = () => TokenizeRequest
type Opts = { tokenize?: (r: TokenizeRequest) => Promise<TokenizeResponse>; debounceMs?: number }

const heuristic = (t: string) => (t.trim() ? Math.ceil(t.length / 4) : 0)

export function useTokenCount(source: Source, opts: Opts = {}) {
  const tokenize = opts.tokenize ?? apiTokenize
  const debounceMs = opts.debounceMs ?? 300
  const positive = ref(0)
  const negative = ref(0)
  const pending = ref(false)
  let reqId = 0
  let hasReal = false
  let timer: ReturnType<typeof setTimeout> | null = null

  async function run() {
    const s = source()
    const id = ++reqId
    pending.value = true
    try {
      const r = await tokenize(s)
      if (id !== reqId) return // superseded by a newer edit — drop this stale response
      positive.value = r.positive
      negative.value = r.negative
      hasReal = true
    } catch {
      // offline / backend down — keep the last known count, no toast spam
    } finally {
      if (id === reqId) pending.value = false
    }
  }

  watch(source, (s) => {
    // Until the first real count, track the heuristic live; after that, hold the real count through
    // the debounce so the number doesn't flicker heuristic↔real on every keystroke.
    if (!hasReal) { positive.value = heuristic(s.positive); negative.value = heuristic(s.negative) }
    if (timer) clearTimeout(timer)
    timer = setTimeout(run, debounceMs)
  }, { immediate: true })

  onScopeDispose(() => { if (timer) clearTimeout(timer) })

  return { positive, negative, pending }
}
