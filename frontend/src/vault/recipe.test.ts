import { describe, expect, it } from 'vitest'
import { placeLabel, recipeOf } from './recipe'

describe('recipe', () => {
  it('says where an image sits', () => {
    expect(placeLabel({ grid_id: 'g', number: 2, heading: 'Harvest moon', section: 'Silver night' }))
      .toBe('Section “Silver night” · “Harvest moon” · album 2')
    expect(placeLabel({ grid_id: 'g', number: 1, heading: null, section: null })).toBe('album 1')
    expect(placeLabel(null)).toBe('Quick access (unsorted)')
  })

  it('lists the blocks in their colours and the settings it has', () => {
    const r = recipeOf({
      positive: '1girl, moon', negative: 'lowres', tags: ['mine', 'moon', 'mine'],
      components: [
        { source: 'library', name: 'Moonlit night', text: 'moon', polarity: 'positive', category: 'scene', tags: [] },
        { source: 'custom', name: '', text: 'lowres, bad hands', polarity: 'negative', category: null, tags: [] },
      ],
      params: { model: 'nai-x', width: 832, height: 1216, steps: 28, scale: 5, seed: 7 },
    }, { work: 'Moon studies', colorOf: (slug) => (slug === 'scene' ? '#579dff' : undefined) })
    expect(r.blocks).toEqual([{ name: 'Moonlit night', color: '#579dff', negative: false },
      { name: 'lowres, bad hands', color: undefined, negative: true }])
    expect(r.settings).toEqual([['Model', 'nai-x'], ['Size', '832×1216'], ['Steps · Guidance', '28 · 5'], ['Seed', '7']])
    expect(r.tags).toEqual(['mine', 'moon'])
  })
})
