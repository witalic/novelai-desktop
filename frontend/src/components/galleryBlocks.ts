// A collapsed Section hides every block after it up to the next Section (section headers always show).
// Returns the ids to hide (rendered with v-show so their DOM/scroll survives the collapse).
export interface BlockLike { id: string; type: string; collapsed?: boolean }
export function hiddenBlockIds<T extends BlockLike>(blocks: T[]): Set<string> {
  const set = new Set<string>()
  let hiding = false
  for (const b of blocks) {
    if (b.type === 'section') { hiding = !!b.collapsed; continue }
    if (hiding) set.add(b.id)
  }
  return set
}
