import { describe, expect, it } from 'vitest'
import { appendX } from './pack'

describe('appendX', () => {
  it('appends after the rightmost block in the lane', () => {
    expect(appendX([{ x: 250, w: 176 }, { x: 440, w: 176 }], 240)).toBe(440 + 176 + 10)
  })
  it('starts just inside an empty lane', () => {
    expect(appendX([], 240)).toBe(252)
  })
})
