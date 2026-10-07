import { computePreviewHighlight } from './previewHighlightProcessor'
import type { PreviewHighlightRequest, PreviewHighlightResponse } from './previewHighlightProtocol'

self.onmessage = async (event: MessageEvent<PreviewHighlightRequest>) => {
  const response: PreviewHighlightResponse = { id: event.data.id }
  try { response.html = await computePreviewHighlight(event.data.source, event.data.language, timing => { response.timing = timing }) }
  catch { /* The caller retains copyable plain text when a grammar fails. */ }
  self.postMessage(response)
}
