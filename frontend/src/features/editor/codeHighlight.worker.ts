import { computeHighlight } from './codeHighlightProcessor'
import type { HighlightRequest, HighlightResponse } from './codeHighlightProtocol'

// The broker sends one job at a time; obsolete jobs never pile up in this realm.
self.onmessage = async (event: MessageEvent<HighlightRequest>) => {
  const response: HighlightResponse = { id: event.data.id }
  try { response.result = await computeHighlight(event.data) }
  catch { /* Plain text remains editable if a grammar cannot be loaded. */ }
  self.postMessage(response, { transfer: response.result ? [response.result.spans.buffer as ArrayBuffer] : [] })
}
