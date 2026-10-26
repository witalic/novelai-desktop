import { describe, expect, it, vi } from 'vitest'
import { announceWorkDeleted, editedWork, onAgentNotice, type AgentNotice } from './useAgentNotices'

vi.mock('vue', () => ({ onUnmounted: () => {} })) // listeners register outside a component here

const notice = (n: Partial<AgentNotice>): AgentNotice => ({ kind: 'work', work_id: 'w-1', message: '', cost: null, ...n })

describe('editedWork', () => {
  it('is the work an agent edited — or another one the same edit changed', () => {
    const moved = notice({ work_id: 'w-to', also: ['w-from'] }) // images moved out of one work into another
    expect(editedWork(moved, 'w-to')).toBe(true)
    expect(editedWork(moved, 'w-from')).toBe(true)
    expect(editedWork(moved, 'w-other')).toBe(false)
    expect(editedWork(notice({}), 'w-1')).toBe(true) // a notice from before `also` existed
  })

  it('a deleted work, the Library or no open work is no edit to catch up on', () => {
    expect(editedWork(notice({ kind: 'work_deleted' }), 'w-1')).toBe(false)
    expect(editedWork(notice({ kind: 'library', work_id: null }), 'w-1')).toBe(false)
    expect(editedWork(notice({}), null)).toBe(false)
  })
})

describe('announceWorkDeleted', () => {
  it('tells every listener, as the app’s own (no agent did it)', () => {
    const heard: AgentNotice[] = []
    onAgentNotice((n) => heard.push(n))
    announceWorkDeleted('w-1')
    expect(heard).toEqual([{ kind: 'work_deleted', work_id: 'w-1', message: '', cost: null, local: true }])
  })
})
