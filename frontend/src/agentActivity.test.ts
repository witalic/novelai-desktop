import { describe, expect, it } from 'vitest'
import { dayLabel, groupByDay, matchesFilter } from './agentActivity'
import type { AgentActivity } from './api'

const row = (over: Partial<AgentActivity>): AgentActivity => ({
  seq: 1, at: '2026-10-25T07:00:00+00:00', kind: 'read', action: 'List works', detail: '', outcome: 'ok', count: 1,
  cost: null, ...over,
})

describe('matchesFilter', () => {
  it('files a refused or failed call only under problems', () => {
    const denied = row({ kind: 'write', outcome: 'denied' })
    expect(matchesFilter(denied, 'problem')).toBe(true)
    expect(matchesFilter(denied, 'write')).toBe(false)
    expect(matchesFilter(denied, 'all')).toBe(true)
  })

  it('files a successful call under its kind', () => {
    const gen = row({ kind: 'generate', cost: 0 })
    expect(['all', 'write', 'generate', 'read', 'problem'].filter((f) => matchesFilter(gen, f as never))).toEqual(['all', 'generate'])
  })
})

describe('day groups', () => {
  const now = new Date(2026, 9, 25, 12, 0) // 25 Oct, local noon

  it('names today and yesterday, and dates older days', () => {
    expect(dayLabel(new Date(2026, 9, 25, 0, 5).toISOString(), now)).toBe('Today')
    expect(dayLabel(new Date(2026, 9, 24, 23, 55).toISOString(), now)).toBe('Yesterday')
    expect(dayLabel(new Date(2026, 9, 20, 9, 0).toISOString(), now)).not.toMatch(/Today|Yesterday/)
  })

  it('puts consecutive rows of one day under one header', () => {
    const rows = [
      row({ seq: 3, at: new Date(2026, 9, 25, 0, 10).toISOString() }),
      row({ seq: 2, at: new Date(2026, 9, 25, 0, 5).toISOString() }),
      row({ seq: 1, at: new Date(2026, 9, 24, 23, 50).toISOString() }),
    ]
    expect(groupByDay(rows, now).map((g) => [g.day, g.rows.map((r) => r.seq)])).toEqual([['Today', [3, 2]], ['Yesterday', [1]]])
  })
})
