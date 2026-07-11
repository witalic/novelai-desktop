/* Pure palette helpers (framework-free, unit-testable) for the prompt widget. The palette's order
 * is the y of the library zone's child nodes; the widget renders it as a list. Keeping these pure
 * lets the reorder and version-drift logic be tested without Vue Flow. */

// Move `moveId` to sit before `beforeId` (null = end), preserving the order of the rest.
export function reorderIds(ids: string[], moveId: string, beforeId: string | null): string[] {
  const rest = ids.filter((id) => id !== moveId)
  if (!ids.includes(moveId)) return ids
  const at = beforeId ? rest.indexOf(beforeId) : -1
  rest.splice(at >= 0 ? at : rest.length, 0, moveId)
  return rest
}

// A pinned copy is stale when its linked Library block has moved on to a higher version.
export function isStale(pinVersion: number | undefined, latestVersion: number | undefined): boolean {
  return (latestVersion ?? 1) > (pinVersion ?? 1)
}
