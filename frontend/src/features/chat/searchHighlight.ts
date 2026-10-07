export function visibleMatchRanges(root: HTMLElement, query: string): Range[] {
  if (!query) return []
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const nodes: Text[] = []
  let node: Node | null
  while ((node = walker.nextNode())) {
    const parent = node.parentElement
    if (parent?.closest('[hidden], .thinking, .tool-result, .context-snapshot, .search-navigation, .message-actions, .usage, .legacy-order-note, time, button, script, style')) continue
    if (node.textContent) nodes.push(node as Text)
  }
  const text = nodes.map(n => n.data).join('').toLowerCase(), term = query.toLowerCase()
  const ranges: Range[] = []
  for (let found = text.indexOf(term); found >= 0 && ranges.length < 1000; found = text.indexOf(term, found + Math.max(1, term.length))) {
    let offset = 0, start: Text | undefined, end: Text | undefined, startOffset = 0, endOffset = 0
    for (const n of nodes) {
      if (!start && offset + n.length > found) { start = n; startOffset = found - offset }
      if (offset + n.length >= found + term.length) { end = n; endOffset = found + term.length - offset; break }
      offset += n.length
    }
    if (start && end) { const range = document.createRange(); range.setStart(start, startOffset); range.setEnd(end, endOffset); ranges.push(range) }
  }
  return ranges
}
export function paintSearchRanges(ranges: Range[], current = 0) {
  const api = globalThis as unknown as { Highlight?: new (...ranges: Range[]) => unknown; CSS?: { highlights?: Map<string, unknown> } }
  api.CSS?.highlights?.delete('opennexus-chat-match'); api.CSS?.highlights?.delete('opennexus-chat-current')
  if (api.Highlight && api.CSS?.highlights && ranges.length) {
    api.CSS.highlights.set('opennexus-chat-match', new api.Highlight(...ranges))
    api.CSS.highlights.set('opennexus-chat-current', new api.Highlight(ranges[current]!))
  }
}
