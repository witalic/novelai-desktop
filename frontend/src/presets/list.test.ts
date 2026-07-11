import { describe, expect, it } from 'vitest'
import { filterPresets } from './list'
import type { Preset, PresetParams } from '../types'

const params = {} as PresetParams
const mk = (id: string, name: string, builtin: boolean): Preset =>
  ({ id, name, params, builtin, favorite: false, is_default: false })

const presets = [
  mk('b1', 'Anime · Full', true), mk('b2', 'Wallpaper', true),
  mk('u1', 'My portrait', false), mk('u2', 'Detailed', false),
]

describe('filterPresets', () => {
  it('splits into builtin and user groups', () => {
    const g = filterPresets(presets, '')
    expect(g.builtin.map((p) => p.id)).toEqual(['b1', 'b2'])
    expect(g.user.map((p) => p.id)).toEqual(['u1', 'u2'])
  })
  it('filters by name across both groups, case-insensitive', () => {
    const g = filterPresets(presets, 'PORTRAIT')
    expect(g.builtin.map((p) => p.id)).toEqual([]) // no built-in matches
    expect(g.user.map((p) => p.id)).toEqual(['u1']) // My portrait
    expect(filterPresets(presets, 'wall').builtin.map((p) => p.id)).toEqual(['b2'])
  })
  it('empty result when nothing matches', () => {
    const g = filterPresets(presets, 'zzz')
    expect(g.builtin).toEqual([])
    expect(g.user).toEqual([])
  })
})
