import { afterEach, describe, expect, it, vi } from 'vitest'

const getVaultConfig = vi.fn()
vi.mock('../api', () => ({
  getVaultConfig: () => getVaultConfig(),
  ApiError: class ApiError extends Error { constructor(public status: number, msg: string) { super(msg) } },
}))

import { useVault, vaultMayBeAway } from './useVault'
import { ApiError } from '../api'

const NAS = '\\\\192.168.8.146\\anime\\NovelAI'
const LOCAL = 'C:\\vault'
const config = (active: string, reachable = true) => ({
  active, proposed_default: LOCAL,
  vaults: [NAS, LOCAL].map((dir) => ({
    dir, initialized: true, writable: true, reachable: dir === active ? reachable : true, active: dir === active,
  })),
})
const settle = () => new Promise((r) => setTimeout(r, 0))

afterEach(async () => { // back to "the NAS is there" for the next test (module state is shared)
  getVaultConfig.mockReset()
  getVaultConfig.mockResolvedValue(config(NAS))
  await useVault().check()
  vi.useRealTimers()
})

describe('useVault', () => {
  it('names the active vault while it is away, retries on its own, and lets what waited go on once it is back', async () => {
    vi.useFakeTimers()
    const back = vi.fn()
    const v = useVault()
    const stop = v.onBack(back)
    getVaultConfig.mockResolvedValue(config(NAS, false))
    await v.check()
    expect(v.unreachable.value).toBe(NAS)
    getVaultConfig.mockResolvedValue(config(NAS)) // the share answers again
    await vi.advanceTimersByTimeAsync(20_000)
    expect(v.unreachable.value).toBeNull()
    expect(back).toHaveBeenCalledTimes(1)
    const calls = getVaultConfig.mock.calls.length
    await vi.advanceTimersByTimeAsync(60_000)
    expect(getVaultConfig).toHaveBeenCalledTimes(calls) // no polling while the vault is there
    stop()
  })

  it('a switch to another vault is no "back": what waited for the NAS never lands in the other one', async () => {
    const back = vi.fn()
    const v = useVault()
    const stop = v.onBack(back)
    getVaultConfig.mockResolvedValue(config(NAS, false))
    await v.check()
    getVaultConfig.mockResolvedValue(config(LOCAL)) // the user made the local vault active
    await v.check()
    expect(v.unreachable.value).toBeNull() // the strip goes…
    expect(back).not.toHaveBeenCalled() // …but nothing that waited is sent there
    stop()
  })

  it('a 503 the next check can’t confirm still lets what failed go on (the share was just waking up)', async () => {
    const back = vi.fn()
    const v = useVault()
    const stop = v.onBack(back)
    getVaultConfig.mockResolvedValue(config(NAS))
    expect(vaultMayBeAway(new ApiError(503, 'The vault folder … can’t be reached.'))).toBe(true)
    await settle()
    expect(v.unreachable.value).toBeNull()
    expect(back).toHaveBeenCalledTimes(1) // the save that failed tries again
    stop()
  })

  it('a check already under way when a call fails is followed by another one', async () => {
    const v = useVault()
    let answer!: (c: unknown) => void
    getVaultConfig.mockImplementationOnce(() => new Promise((r) => { answer = r }))
    const running = v.check()
    getVaultConfig.mockResolvedValue(config(NAS, false)) // the share dropped meanwhile
    vaultMayBeAway(new ApiError(503, 'x'))
    answer(config(NAS)) // the first check's answer predates the failure
    await running
    await settle()
    expect(v.unreachable.value).toBe(NAS)
  })

  it('a backend that is not answering says nothing about the vault, and only a 503 is a sign', async () => {
    const v = useVault()
    getVaultConfig.mockRejectedValue(new Error('connection refused'))
    await v.check()
    expect(v.unreachable.value).toBeNull()
    expect(vaultMayBeAway(new ApiError(500, 'boom'))).toBe(false)
    expect(vaultMayBeAway(new Error('offline'))).toBe(false)
  })
})
