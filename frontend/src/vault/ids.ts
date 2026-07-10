// Type-prefixed unique ids for vault objects (work-…, block-…, img-…), matching the backend's
// category ids (cat-…). The prefix keeps ids self-describing and collision-proof across object types.
export function newId(prefix: string): string {
  const uuid = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : `${Date.now().toString(16)}-${Math.floor(Math.random() * 0xffffffff).toString(16)}`
  return `${prefix}-${uuid}`.replace(/[^A-Za-z0-9_-]/g, '')
}
