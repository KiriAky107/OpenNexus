import { getCodeTokenizer } from '@/services/codeHighlighter'
import { MAX_HIGHLIGHT_CHARACTERS, MAX_HIGHLIGHT_SPANS, tokenStyle, type HighlightInput, type HighlightResult } from './codeHighlightProtocol'

/** Full-document tokenization preserves comments, strings and embedded grammars. */
export async function computeHighlight(input: HighlightInput): Promise<HighlightResult | undefined> {
  if (input.source.length > MAX_HIGHLIGHT_CHARACTERS) return
  const tokenize = await getCodeTokenizer(input.theme, input.language)
  const tokens = tokenize(input.source, input.language)
  const spans: number[] = [], styles: string[] = [], styleIds = new Map<string, number>()
  for (const line of tokens) for (const token of line) {
    if (!token.content.length) continue
    if (spans.length / 3 >= MAX_HIGHLIGHT_SPANS) return
    const style = tokenStyle(token.color, token.fontStyle)
    let id = styleIds.get(style)
    if (id === undefined) { id = styles.length; styleIds.set(style, id); styles.push(style) }
    spans.push(token.offset, token.offset + token.content.length, id)
  }
  return { spans: new Uint32Array(spans), styles }
}
