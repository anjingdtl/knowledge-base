/** Parse a JSON-encoded tags string into a string array. Returns [] on failure. */
export function safeTags(raw: string): string[] {
  try { return JSON.parse(raw || '[]') } catch { return [] }
}
