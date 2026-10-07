export const MAX_PREVIEW_SOURCE = 1_000_000
export const MAX_PREVIEW_HTML = 8_000_000
export interface PreviewHighlightRequest { id: number; source: string; language: string }
// These are elapsed worker-clock durations, not OS CPU usage. The synchronous
// tokenizer duration is separated from asynchronous grammar loading.
export interface PreviewHighlightTiming { language_load_ms: number; highlight_ms: number; worker_ms: number }
export interface PreviewHighlightResponse { id: number; html?: string; timing?: PreviewHighlightTiming }

export function plainCode(source: string): string {
  const escaped = source.replace(/[&<>]/g, value => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[value]!)
  return `<pre class="shiki"><code>${escaped.split(/\r?\n/).map(line => `<span class="line">${line}</span>`).join('\n')}</code></pre>`
}
