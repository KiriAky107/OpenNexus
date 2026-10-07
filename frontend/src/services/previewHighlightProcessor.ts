import { loadCodeLanguage } from './codeHighlighter'
import { MAX_PREVIEW_SOURCE, MAX_PREVIEW_HTML, type PreviewHighlightTiming } from './previewHighlightProtocol'

export async function computePreviewHighlight(source: string, requestedLanguage: string, measured?: (timing: PreviewHighlightTiming) => void): Promise<string | undefined> {
  if (source.length > MAX_PREVIEW_SOURCE) return
  const start = performance.now()
  const { shiki, language } = await loadCodeLanguage(requestedLanguage)
  const loaded = performance.now()
  const html = shiki.codeToHtml(source, {
    lang: language, themes: { light: 'github-light', dark: 'github-dark' }, defaultColor: false,
  })
  const done = performance.now()
  measured?.({ language_load_ms: loaded - start, highlight_ms: done - loaded, worker_ms: done - start })
  return html.length <= MAX_PREVIEW_HTML ? html : undefined
}
