export const MAX_PREVIEW_SOURCE = 1_000_000
export const MAX_PREVIEW_HTML = 8_000_000
export interface PreviewHighlightRequest { id: number; source: string; language: string }
export interface PreviewHighlightResponse { id: number; html?: string }

export function plainCode(source: string): string {
  const escaped = source.replace(/[&<>]/g, value => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[value]!)
  return `<pre class="shiki"><code>${escaped.split(/\r?\n/).map(line => `<span class="line">${line}</span>`).join('\n')}</code></pre>`
}
