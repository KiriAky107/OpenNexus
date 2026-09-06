import { apiClient } from './apiClient'
import DOMPurify from 'dompurify'

interface PlotWire {
  result: { content: string; width: number; height: number; warnings: string[] } | null
  diagnostics: { message: string; severity: string; line?: number }[]
  node_count: number
}
const cache = new Map<string, Promise<{ svg: string; warnings: string[]; nodeCount: number }>>()
export function renderFunctionPlot(source: string, themeId = 'light') {
  const key = JSON.stringify([source, themeId])
  if (cache.has(key)) return cache.get(key)!
  const result = apiClient.post<PlotWire>('/api/plots/function', { source, theme_id: themeId }, { timeoutMs: 30000 }).then(wire => ({
    svg: DOMPurify.sanitize(wire.result?.content ?? '', { USE_PROFILES: { svg: true } }),
    warnings: [...wire.diagnostics.map(d => `${d.message}${d.line ? ` (行 ${d.line})` : ''}`), ...(wire.result?.warnings ?? [])],
    nodeCount: wire.node_count,
  })).catch(error => { cache.delete(key); throw error })
  if (cache.size >= 32) cache.delete(cache.keys().next().value!)
  cache.set(key, result)
  return result
}
