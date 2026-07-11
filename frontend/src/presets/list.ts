/* Pure grouping/filter for the Presets tab (framework-free, unit-tested). */
import type { Preset } from '../types'

export function filterPresets(presets: Preset[], query: string): { builtin: Preset[]; user: Preset[] } {
  const q = query.trim().toLowerCase()
  const match = (p: Preset) => !q || p.name.toLowerCase().includes(q)
  return {
    builtin: presets.filter((p) => p.builtin && match(p)),
    user: presets.filter((p) => !p.builtin && match(p)),
  }
}
