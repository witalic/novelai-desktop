import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, reactive } from 'vue'
import { useTokenCount } from './useTokenCount'
import type { TokenizeRequest, TokenizeResponse } from '../types'

/* The token-usage indicator's data layer: debounce coalescing, stale-guard, heuristic placeholder. */

const resp = (positive: number, negative = 0): TokenizeResponse => ({ positive, negative, tokenizer: 't5' })
const tick = () => new Promise<void>((r) => { setTimeout(r, 0); vi.advanceTimersByTime(0) })

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('useTokenCount', () => {
  it('shows a heuristic immediately, then the real count after the debounce', async () => {
    const tokenize = vi.fn(async () => resp(42, 7))
    const scope = effectScope()
    const src = reactive({ model: 'm', positive: 'a'.repeat(40), negative: 'b'.repeat(8) })
    const c = scope.run(() => useTokenCount(() => ({ ...src }), { tokenize }))!
    expect(c.positive.value).toBe(10) // heuristic 40/4 before any request resolves
    expect(tokenize).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(300)
    expect(tokenize).toHaveBeenCalledTimes(1)
    expect(c.positive.value).toBe(42)
    expect(c.negative.value).toBe(7)
    scope.stop()
  })

  it('debounces rapid edits into a single request', async () => {
    const tokenize = vi.fn(async () => resp(5))
    const scope = effectScope()
    const src = reactive({ model: 'm', positive: 'a', negative: '' })
    scope.run(() => useTokenCount(() => ({ ...src }), { tokenize }))
    src.positive = 'ab'; await tick()
    src.positive = 'abc'; await tick()
    src.positive = 'abcd'; await tick()
    expect(tokenize).not.toHaveBeenCalled() // still within the debounce window
    await vi.advanceTimersByTimeAsync(300)
    expect(tokenize).toHaveBeenCalledTimes(1)
    expect(tokenize).toHaveBeenLastCalledWith(expect.objectContaining({ positive: 'abcd' }))
    scope.stop()
  })

  it('drops a slow earlier response that resolves after a newer one', async () => {
    let resolveFirst!: (v: TokenizeResponse) => void
    const first = new Promise<TokenizeResponse>((r) => { resolveFirst = r })
    const tokenize = vi.fn<(r: TokenizeRequest) => Promise<TokenizeResponse>>()
      .mockReturnValueOnce(first)                 // slow first request
      .mockResolvedValueOnce(resp(99))            // fast newer request
    const scope = effectScope()
    const src = reactive({ model: 'm', positive: 'x', negative: '' })
    const c = scope.run(() => useTokenCount(() => ({ ...src }), { tokenize }))!
    await vi.advanceTimersByTimeAsync(300)         // fires request 1 (hangs)
    src.positive = 'xy'; await tick()
    await vi.advanceTimersByTimeAsync(300)         // fires request 2 (fast) → resolves to 99
    expect(c.positive.value).toBe(99)
    resolveFirst(resp(1))                          // stale first lands last…
    await tick()
    expect(c.positive.value).toBe(99)              // …and is discarded
    scope.stop()
  })

  it('keeps the last count when the request errors (offline)', async () => {
    const tokenize = vi.fn<(r: TokenizeRequest) => Promise<TokenizeResponse>>()
      .mockResolvedValueOnce(resp(12))
      .mockRejectedValueOnce(new Error('offline'))
    const scope = effectScope()
    const src = reactive({ model: 'm', positive: 'x', negative: '' })
    const c = scope.run(() => useTokenCount(() => ({ ...src }), { tokenize }))!
    await vi.advanceTimersByTimeAsync(300)
    expect(c.positive.value).toBe(12)
    src.positive = 'xyz'; await tick()
    await vi.advanceTimersByTimeAsync(300)
    expect(c.positive.value).toBe(12) // error swallowed, last count preserved
    scope.stop()
  })
})
