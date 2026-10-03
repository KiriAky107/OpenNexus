/** Shared store/tree path format. Inputs are vault paths, not URLs or host paths. */
export function normalizeWorkspacePath(path: string): string {
  const normalized = path.replace(/\\/g, '/').replace(/^\/+|\/+$/g, '')
  return normalized ? `/${normalized}` : '/'
}
