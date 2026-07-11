/* Canvas placement helpers (framework-free, unit-testable). */

// X for appending a block at the end of a station lane's x-order — prompt order stays predictable
// when the widget's ＋ copies a palette block into the lane.
export function appendX(existing: { x: number; w: number }[], laneStartX: number, gap = 10): number {
  return existing.length ? Math.max(...existing.map((b) => b.x + b.w)) + gap : laneStartX + 12
}
