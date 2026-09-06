import { apiClient } from './apiClient'
import { Marked } from 'marked'
import { renderMermaid } from './mermaidService'

export type ExportFormat = 'html' | 'pdf' | 'docx'
interface JobWire {
  job_id: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  warnings: string[]; error: string | null
  file: { file_name: string; size: number } | null
}
export interface ExportJob { id: string; status: JobWire['status']; warnings: string[]; error: string | null; fileName?: string }
const mapJob = (w: JobWire): ExportJob => ({ id: w.job_id, status: w.status, warnings: w.warnings, error: w.error, fileName: w.file?.file_name })
export async function rasterize(svg: string, signal?: AbortSignal): Promise<string> {
  const doc = new DOMParser().parseFromString(svg, 'image/svg+xml')
  const root = doc.documentElement
  const box = root.getAttribute('viewBox')?.split(/[ ,]+/).map(Number)
  const width = box?.[2] || 800, height = box?.[3] || 600
  if (!Number.isFinite(width + height) || width <= 0 || height <= 0) throw new Error('图表尺寸无效')
  const scale = Math.min(4, Math.max(2, 1200 / width), Math.sqrt(4_000_000 / (width * height)))
  root.setAttribute('width', String(Math.floor(width * scale))); root.setAttribute('height', String(Math.floor(height * scale)))
  root.style.maxWidth = 'none'
  const data = new XMLSerializer().serializeToString(root)
  const image = new Image()
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(data)}`
  await new Promise<void>((resolve, reject) => {
    const abort = () => reject(new DOMException('Aborted', 'AbortError'))
    const timer = setTimeout(() => reject(new Error('图表图片解码超时')), 15000)
    const cleanup = () => { clearTimeout(timer); signal?.removeEventListener('abort', abort) }
    if (signal?.aborted) { cleanup(); abort(); return }
    signal?.addEventListener('abort', abort, { once: true })
    image.decode().then(resolve, reject).finally(cleanup)
  })
  const canvas = document.createElement('canvas')
  canvas.width = Math.floor(width * scale); canvas.height = Math.floor(height * scale)
  const context = canvas.getContext('2d')!
  context.fillStyle = '#ffffff'; context.fillRect(0, 0, canvas.width, canvas.height)
  context.drawImage(image, 0, 0, canvas.width, canvas.height)
  return canvas.toDataURL('image/png').split(',')[1]!
}
export async function hashSource(source: string) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(source.trim())))].map(v => v.toString(16).padStart(2, '0')).join('')
}
export const exportService = {
  async create(markdown: string, title: string, format: ExportFormat, options: { theme_id: string; include_title: boolean; page_size: string }, signal?: AbortSignal, filePath?: string) {
    const blocks: string[] = []
    const parser = new Marked()
    parser.walkTokens(parser.lexer(markdown), token => { if (token.type === 'code' && token.lang === 'mermaid') blocks.push(token.text) })
    const assets = []
    for (const source of [...new Set(blocks)]) {
      signal?.throwIfAborted()
      if (assets.length >= 16) throw new Error('每次导出最多 16 个 Mermaid 图表')
      const result = await renderMermaid(source, { mode: 'raster', theme: 'light' })
      if (result.warnings.length) throw new Error(`Mermaid 无法导出：${result.warnings.join('; ')}`)
      assets.push({ kind: 'mermaid', source_hash: await hashSource(source), png_base64: await rasterize(result.svg, signal) })
    }
    signal?.throwIfAborted()
    return mapJob(await apiClient.post<JobWire>('/api/exports', { source: { type: 'markdown', markdown, file_path: filePath }, title, format, options, assets }))
  },
  async get(id: string) { return mapJob(await apiClient.get<JobWire>(`/api/exports/${encodeURIComponent(id)}`)) },
  async list() { const response = await apiClient.get<{ items: JobWire[] }>('/api/exports'); return response.items.map(mapJob) },
  cancel(id: string) { return apiClient.post(`/api/exports/${encodeURIComponent(id)}/cancel`) },
  async download(job: ExportJob) {
    const response = await apiClient.get<Response>(`/api/exports/${encodeURIComponent(job.id)}/file`)
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a'); link.href = url; link.download = job.fileName ?? 'export'
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
  },
}
