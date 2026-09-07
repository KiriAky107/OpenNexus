import { apiClient } from './apiClient'
import DOMPurify from 'dompurify'

interface PlotWire {
  result: { content: string; width: number; height: number; warnings: string[] } | null
  diagnostics: { message: string; severity: string; line?: number }[]
  node_count: number
}
const cache = new Map<string, Promise<{ svg: string; warnings: string[]; nodeCount: number }>>()
export function renderFunctionPlot(source: string, themeId = 'light') {
  // 缓存 Promise 既合并并发的相同请求，也避免重复渲染；失败结果立即移除以允许重试。
  const key = JSON.stringify([source, themeId])
  if (cache.has(key)) return cache.get(key)!
  const result = apiClient.post<PlotWire>('/api/plots/function', { source, theme_id: themeId }, { timeoutMs: 30000 }).then(wire => ({
    // 后端只生成静态 SVG，前端仍在渲染边界执行净化，防止未来响应扩展引入可执行标记。
    svg: DOMPurify.sanitize(wire.result?.content ?? '', { USE_PROFILES: { svg: true } }),
    warnings: [...wire.diagnostics.map(d => `${d.message}${d.line ? ` (行 ${d.line})` : ''}`), ...(wire.result?.warnings ?? [])],
    nodeCount: wire.node_count,
  })).catch(error => { cache.delete(key); throw error })
  if (cache.size >= 32) cache.delete(cache.keys().next().value!)
  cache.set(key, result)
  return result
}
