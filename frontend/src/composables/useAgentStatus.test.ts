import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgentActivity, AgentStatus } from '../api'

const getAgent = vi.fn()
const clearAgentActivity = vi.fn()
vi.mock('../api', () => ({
  getAgent: () => getAgent(),
  clearAgentActivity: () => clearAgentActivity(),
  patchAgent: vi.fn(),
  regenerateAgentToken: vi.fn(),
}))

const entry = (seq: number, outcome: AgentActivity['outcome'] = 'ok'): AgentActivity => ({
  seq, at: '2026-10-25T07:00:00+00:00', kind: 'read', action: 'List works', detail: '', outcome, count: 1, cost: null,
})
const status = (activity: AgentActivity[]): AgentStatus => ({
  config: { enabled: true, permissions: {} as never, spending: {} as never }, running: true, address: '', note: null,
  error: null, connected: 0, token_hint: null, spent_today: 0, activity,
})
const deferred = <T,>() => {
  let resolve!: (v: T) => void
  return { promise: new Promise<T>((r) => (resolve = r)), resolve }
}

// The composable is a singleton: each test gets a fresh copy of the module.
async function fresh() {
  vi.resetModules()
  return (await import('./useAgentStatus')).useAgentStatus()
}

beforeEach(() => {
  vi.useFakeTimers()
  getAgent.mockReset()
  clearAgentActivity.mockReset()
})
afterEach(() => {
  vi.clearAllTimers() // the poll each fresh module started
  vi.useRealTimers()
})

describe('useAgentStatus', () => {
  it('counts what arrived since Agent activity was last on screen', async () => {
    getAgent.mockResolvedValue(status([entry(2, 'denied'), entry(1)]))
    const a = await fresh()
    await vi.waitFor(() => expect(a.status.value).not.toBeNull())
    expect([a.freshCount.value, a.freshProblem.value]).toEqual([2, true])
    a.markSeen()
    expect(a.freshCount.value).toBe(0)
    getAgent.mockResolvedValue(status([entry(3), entry(2, 'denied'), entry(1)]))
    await a.load()
    expect([a.freshCount.value, a.freshProblem.value]).toEqual([1, false])
  })

  it('forgets what was seen when a restarted backend counts from 1 again', async () => {
    getAgent.mockResolvedValue(status([entry(9)]))
    const a = await fresh()
    await vi.waitFor(() => expect(a.status.value).not.toBeNull())
    a.markSeen()
    getAgent.mockResolvedValue(status([entry(1)]))
    await a.load()
    expect(a.freshCount.value).toBe(1)
  })

  it('keeps the newest answer when an older poll lands late', async () => {
    getAgent.mockResolvedValue(status([]))
    const a = await fresh()
    await vi.waitFor(() => expect(a.status.value).not.toBeNull())
    const slow = deferred<AgentStatus>()
    getAgent.mockReturnValueOnce(slow.promise)
    const polling = a.load()
    clearAgentActivity.mockResolvedValue(status([entry(5)]))
    expect(await a.clear()).toBe(true)
    slow.resolve(status([entry(4)]))
    await polling
    expect(a.status.value?.activity.map((e) => e.seq)).toEqual([5])
  })

  it('keeps retrying until a first read succeeds, then polls only while the listener runs', async () => {
    getAgent.mockRejectedValueOnce(new Error('backend not up yet'))
    const a = await fresh()
    await vi.waitFor(() => expect(getAgent).toHaveBeenCalledTimes(1))
    getAgent.mockResolvedValue({ ...status([]), running: false })
    await vi.advanceTimersByTimeAsync(5000)
    expect(a.status.value?.running).toBe(false)
    await vi.advanceTimersByTimeAsync(15000)
    expect(getAgent).toHaveBeenCalledTimes(2) // off: no more polls
  })
})
