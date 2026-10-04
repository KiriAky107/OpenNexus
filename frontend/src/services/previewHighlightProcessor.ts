import { loadCodeLanguage } from './codeHighlighter'
import { MAX_PREVIEW_SOURCE, MAX_PREVIEW_HTML } from './previewHighlightProtocol'

export async function computePreviewHighlight(source: string, requestedLanguage: string): Promise<string | undefined> {
  if (source.length > MAX_PREVIEW_SOURCE) return
  const { shiki, language } = await loadCodeLanguage(requestedLanguage)
  const html = shiki.codeToHtml(source, {
    lang: language, themes: { light: 'github-light', dark: 'github-dark' }, defaultColor: false,
  })
  return html.length <= MAX_PREVIEW_HTML ? html : undefined
}
