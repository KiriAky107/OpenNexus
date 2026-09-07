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
export type ExportPalette = Record<'page' | 'surface' | 'text' | 'muted' | 'code' | 'border' | 'accent', string>
// 冻结导出开始时的主题颜色，避免后台兼容渲染受到后续主题切换影响。
export function captureExportPalette(): ExportPalette | undefined {
  const style = getComputedStyle(document.documentElement)
  const tokens = { page:'background-primary', surface:'surface-primary', text:'text-primary', muted:'text-secondary', code:'background-secondary', border:'border-default', accent:'accent-primary' }
  const entries = Object.entries(tokens).map(([key, token]) => {
    const value = style.getPropertyValue(`--color-${token}`).trim()
    if (/^#[0-9a-f]{6}$/i.test(value)) return [key,value]
    if (/^#[0-9a-f]{3}$/i.test(value)) return [key, '#' + [...value.slice(1)].map(c => c+c).join('')]
    const rgb = value.match(/^rgb\(\s*(\d+)[, ]+\s*(\d+)[, ]+\s*(\d+)\s*\)$/)
    if (rgb) return [key, '#' + rgb.slice(1,4).map(v => Number(v).toString(16).padStart(2,'0')).join('')]
    // 借助浏览器解析命名色、color-mix、OKLCH 和透明色，再冻结为可移植 RGB 色板。
    if (typeof CSS !== 'undefined' && CSS.supports('color', value)) {
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1
      const context = canvas.getContext('2d')
      if (context) {
        context.fillStyle = '#ffffff'; context.fillRect(0,0,1,1)
        context.fillStyle = value; context.fillRect(0,0,1,1)
        const pixel = context.getImageData(0,0,1,1).data
        return [key, '#' + [...pixel.slice(0,3)].map(v => v.toString(16).padStart(2,'0')).join('')]
      }
    }
    return [key, '']
  })
  return entries.every(([,value]) => value) ? Object.fromEntries(entries) as ExportPalette : undefined
}
export async function rasterize(svg: string, signal?: AbortSignal, unlimited = false, background = '#ffffff'): Promise<string> {
  // 非 PDF 格式保留像素预算和解码超时；PDF 的自包含快照解除资源配额。
  const doc = new DOMParser().parseFromString(svg, 'image/svg+xml')
  const root = doc.documentElement
  const box = root.getAttribute('viewBox')?.split(/[ ,]+/).map(Number)
  const width = box?.[2] || 800, height = box?.[3] || 600
  if (!Number.isFinite(width + height) || width <= 0 || height <= 0) throw new Error('图表尺寸无效')
  const scale = Math.min(4, Math.max(2, 1200 / width), unlimited ? Infinity : Math.sqrt(4_000_000 / (width * height)))
  root.setAttribute('width', String(Math.floor(width * scale))); root.setAttribute('height', String(Math.floor(height * scale)))
  root.style.maxWidth = 'none'
  const data = new XMLSerializer().serializeToString(root)
  const image = new Image()
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(data)}`
  await new Promise<void>((resolve, reject) => {
    const abort = () => reject(new DOMException('Aborted', 'AbortError'))
    const timer = unlimited ? undefined : setTimeout(() => reject(new Error('图表图片解码超时')), 15000)
    const cleanup = () => { clearTimeout(timer); signal?.removeEventListener('abort', abort) }
    if (signal?.aborted) { cleanup(); abort(); return }
    signal?.addEventListener('abort', abort, { once: true })
    image.decode().then(resolve, reject).finally(cleanup)
  })
  const canvas = document.createElement('canvas')
  canvas.width = Math.floor(width * scale); canvas.height = Math.floor(height * scale)
  const context = canvas.getContext('2d')!
  context.fillStyle = background; context.fillRect(0, 0, canvas.width, canvas.height)
  context.drawImage(image, 0, 0, canvas.width, canvas.height)
  return canvas.toDataURL('image/png').split(',')[1]!
}
export async function hashSource(source: string) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(source.trim())))].map(v => v.toString(16).padStart(2, '0')).join('')
}
export const exportService = {
  async create(markdown: string, title: string, format: ExportFormat, options: { theme_id: string; include_title: boolean; page_size: string; palette?: ExportPalette }, signal?: AbortSignal, filePath?: string) {
    let printHtml: string | undefined
    if (format === 'pdf') {
      const { preparePdfSnapshot } = await import('./pdfSnapshotService')
      printHtml = await preparePdfSnapshot(markdown,title,options,signal,filePath)
    }
    const blocks: string[] = []
    const parser = new Marked()
    if (format !== 'pdf') parser.walkTokens(parser.lexer(markdown), token => { if (token.type === 'code' && token.lang?.trim().split(/\s+/)[0]?.toLowerCase() === 'mermaid') blocks.push(token.text) })
    const assets = []
    for (const source of [...new Set(blocks)]) {
      signal?.throwIfAborted()
      if (format !== 'pdf' && assets.length >= 16) throw new Error('每次导出最多 16 个 Mermaid 图表')
      const pdf = format === 'pdf'
      const result = await renderMermaid(source, pdf ? { mode: 'raster', theme: ['dark','midnight-purple'].includes(options.theme_id) ? 'dark' : 'light', palette: options.palette, unlimited: true } : { mode: 'raster', theme: 'light' })
      if (result.warnings.length) throw new Error(`Mermaid 无法导出：${result.warnings.join('; ')}`)
      assets.push({ kind: 'mermaid', source_hash: await hashSource(source), png_base64: await rasterize(result.svg, signal, pdf, pdf ? options.palette?.surface ?? (['dark','midnight-purple'].includes(options.theme_id) ? '#161b22' : '#ffffff') : '#ffffff') })
    }
    signal?.throwIfAborted()
    // 提交期间收到取消时仍等待服务器返回任务句柄；只中断 HTTP 会遗留无法追踪的后台任务。
    const job = mapJob(await apiClient.post<JobWire>('/api/exports', { source: { type: 'markdown', markdown, file_path: filePath }, title, format, options, assets, ...(printHtml ? { print_html:printHtml } : {}) }))
    if (signal?.aborted) {
      await apiClient.post(`/api/exports/${encodeURIComponent(job.id)}/cancel`)
      const current = await apiClient.get<JobWire>(`/api/exports/${encodeURIComponent(job.id)}`)
      if (current.status === 'completed') throw new Error('导出已完成，无法取消；请在任务列表中下载。')
      if (current.status === 'failed') throw new Error(current.error || '导出任务已失败，请查看任务列表。')
      signal.throwIfAborted()
    }
    return job
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
