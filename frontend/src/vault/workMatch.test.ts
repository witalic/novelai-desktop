import { describe, expect, it } from 'vitest'
import { highlight, matchParts } from './workMatch'

const said = (parts: { text: string; hit: boolean }[]) => parts.map((p) => (p.hit ? `[${p.text}]` : p.text)).join('')

describe('workMatch', () => {
  it('marks every place the search was found, in any case', () => {
    expect(said(highlight('Harvest Moon over the moon', 'MOON'))).toBe('Harvest [Moon] over the [moon]')
    expect(said(highlight('Луна', 'луна'))).toBe('[Луна]')
    expect(highlight('Plain', ' ')).toEqual([{ text: 'Plain', hit: false }])
  })

  it('says where a work matched when its title did not', () => {
    expect(said(matchParts({ kind: 'section', text: 'Harvest moon', count: 0 }, 'moon'))).toBe('Section “Harvest [moon]”')
    expect(said(matchParts({ kind: 'note', text: 'Try the comet', count: 0 }, 'comet'))).toBe('Note “Try the [comet]”')
    expect(said(matchParts({ kind: 'images', text: null, count: 31 }, ' moon '))).toBe('“[moon]” in 31 images')
    expect(said(matchParts({ kind: 'images', text: null, count: 1 }, 'moon'))).toBe('“[moon]” in 1 image')
  })
})
