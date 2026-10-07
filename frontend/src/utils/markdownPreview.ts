import DOMPurify from 'dompurify'
import { appendCodeToolbar, createMarkdownParser, highlightCode, renderMarkdownFragment, type MarkdownRenderOptions } from './markdown'

interface CodeBlock {
  source: string
  language: string
  wrapper: HTMLElement
  pre: HTMLElement
  code: HTMLElement
  lines: HTMLElement[]
  colored?: DocumentFragment
  coloredLines?: HTMLElement[]
  coloredChunk?: HTMLElement
  html?: string[]
  nextLine: number
  nextColoredLine: number
  complete: boolean
}
interface Block {
  key: string
  nodes: Node[]
  codes: CodeBlock[]
}

// A task boundary lets input and painting run even in an unfocused WebView.
// requestAnimationFrame alone can stop progressing in background windows.
export function yieldPreview(signal?: AbortSignal): Promise<void> {
  signal?.throwIfAborted()
  return new Promise((resolve, reject) => {
    // MessageChannel avoids the browser's nested-timer minimum delay while
    // still yielding a task to input/paint. Only one task per caller is queued.
    const channel = typeof MessageChannel === 'undefined' ? undefined : new MessageChannel()
    let timer: ReturnType<typeof setTimeout> | undefined
    const cleanup = () => {
      clearTimeout(timer); channel?.port1.close(); channel?.port2.close()
      signal?.removeEventListener('abort', abort)
    }
    const abort = () => { cleanup(); reject(signal?.reason) }
    const done = () => { cleanup(); resolve() }
    if (channel) { channel.port1.onmessage = done; channel.port2.postMessage(null) }
    else timer = setTimeout(done, 0)
    signal?.addEventListener('abort', abort, { once: true })
  })
}

async function createCode(source: string, language: string, signal?: AbortSignal): Promise<CodeBlock> {
  const wrapper = document.createElement('div')
  wrapper.className = 'markdown-code-block'
  wrapper.dataset.languageLabel = language
  wrapper.dataset.highlightState = 'plain'
  const pre = document.createElement('pre')
  pre.className = 'shiki'
  const code = document.createElement('code')
  pre.append(code); wrapper.append(pre)
  appendCodeToolbar(wrapper, language, source)
  const lines = source.split(/\r?\n/)
  const nodes: HTMLElement[] = []
  let chunk: HTMLElement | undefined
  let start = performance.now()
  for (let index = 0; index < lines.length; index++) {
    const line = document.createElement('span')
    line.className = 'line'; line.textContent = lines[index]!
    line.dataset.previewLineNumber = String(index + 1)
    if (index % 100 === 0) { chunk = createChunk(Math.min(100, lines.length - index)); code.append(chunk) }
    chunk!.append(line); nodes.push(line)
    if (index % 100 === 99 && performance.now() - start > 6) {
      await yieldPreview(signal); start = performance.now()
    }
  }
  signal?.throwIfAborted()
  return { source, language, wrapper, pre, code, lines: nodes, nextLine: 0, nextColoredLine: 0, complete: false }
}

function createChunk(lines: number) {
  const chunk = document.createElement('span')
  chunk.className = 'markdown-code-chunk'
  chunk.style.setProperty('--preview-line-count', String(lines))
  return chunk
}

function codeReadingAnchor(block: CodeBlock) {
  const view = block.pre.ownerDocument.defaultView
  if (!view || !block.pre.isConnected) return
  for (let viewport = block.pre; viewport; viewport = viewport.parentElement!) {
    if (!/^(auto|scroll|overlay)$/.test(view.getComputedStyle(viewport).overflowY)
        || viewport.scrollHeight <= viewport.clientHeight) continue
    const code = block.pre.getBoundingClientRect(), area = viewport.getBoundingClientRect()
    const top = Math.max(0, code.top, area.top), bottom = Math.min(view.innerHeight, code.bottom, area.bottom)
    const left = Math.max(0, code.left, area.left), right = Math.min(view.innerWidth, code.right, area.right)
    if (bottom <= top || right <= left) return
    const line = block.pre.ownerDocument.elementFromPoint?.((left + right) / 2, (top + bottom) / 2)?.closest('.line')
    if (line && block.code.contains(line)) return { line, viewport, top: line.getBoundingClientRect().top }
    return
  }
}

function retainCodeAnchor(anchor: ReturnType<typeof codeReadingAnchor>) {
  if (!anchor?.line.isConnected) return
  const delta = anchor.line.getBoundingClientRect().top - anchor.top
  if (Math.abs(delta) > 0.01) anchor.viewport.scrollTop += delta
}

// Shiki's bounded worker output has one balanced line span per newline. Each
// slice is sanitized independently before touching live DOM; user code is
// always text, and the original source stays available for exact copying.
async function colorCode(block: CodeBlock, signal?: AbortSignal) {
  if (block.complete) return
  block.wrapper.dataset.highlightState = 'pending'
  if (!block.html) {
    const html = await highlightCode(block.source, block.language, signal)
    signal?.throwIfAborted()
    const opening = html.indexOf('<code>'), closing = html.lastIndexOf('</code>')
    if (opening < 0 || closing < opening || !html.startsWith('<pre')) throw new Error('Invalid preview code frame')
    const header = DOMPurify.sanitize(html.slice(0, opening) + '<code></code></pre>', { RETURN_DOM_FRAGMENT: true })
    const pre = header.querySelector('pre')
    if (!pre) throw new Error('Invalid preview code header')
    // Only generated, sanitized appearance attributes belong on the code frame.
    for (const name of ['class', 'style', 'tabindex']) {
      const value = pre.getAttribute(name)
      if (value === null) block.pre.removeAttribute(name)
      else block.pre.setAttribute(name, value)
    }
    block.html = html.slice(opening + 6, closing).split('\n')
    if (block.html.length !== block.lines.length) throw new Error('Preview line count differs from source')
    block.colored = document.createDocumentFragment(); block.coloredLines = []
  }
  while (block.nextLine < block.html.length) {
    signal?.throwIfAborted()
    // Cap both row count and HTML size. A single very long row falls back to
    // escaped text rather than spending a task on thousands of token spans.
    const start = block.nextLine
    let end = start, bytes = 0
    while (end < block.html.length && end - start < 32) {
      const size = block.html[end]!.length
      if (end > start && bytes + size > 24_000) break
      bytes += size; end++
    }
    const slice = block.html.slice(start, end).map((html, offset) => html.length > 24_000
      ? `<span class="line">${escapeText(block.lines[start + offset]!.textContent ?? '')}</span>` : html).join('\n')
    const fragment = DOMPurify.sanitize(`<pre><code>${slice}</code></pre>`, { RETURN_DOM_FRAGMENT: true })
    const lines = fragment.querySelector('code')?.children
    if (!lines || lines.length !== end - start || [...lines].some(line => !line.matches('span.line'))) {
      throw new Error('Invalid preview code lines')
    }
    for (const line of [...lines]) {
      if (block.nextLine % 100 === 0) {
        block.coloredChunk = createChunk(Math.min(100, block.lines.length - block.nextLine))
        block.colored!.append(block.coloredChunk)
      }
      ;(line as HTMLElement).dataset.previewLineNumber = String(block.nextLine + 1)
      block.nextLine++
      block.coloredChunk!.append(line); block.coloredLines!.push(line as HTMLElement)
    }
    // Sanitize in detached batches before applying tokens to the existing rows.
    if (block.nextLine < block.html.length) await yieldPreview(signal)
  }
  // Keep the measured chunks and reading-anchor rows. Replacing their parent
  // discards intrinsic heights and displaces a reader in wrapped code. Commit
  // tokens in bounded tasks; progress survives cancellation without replaying
  // already moved children or clearing rows on the next render.
  let committed = 0, anchor = codeReadingAnchor(block), commitStart = performance.now()
  while (block.nextColoredLine < block.lines.length) {
    signal?.throwIfAborted()
    const index = block.nextColoredLine
    const colored = block.coloredLines![index]!, line = block.lines[index]!
    line.replaceChildren(...colored.childNodes)
    const style = colored.getAttribute('style')
    if (style !== null) line.setAttribute('style', style)
    block.nextColoredLine++; committed++
    if (block.nextColoredLine < block.lines.length && (committed >= 32 || performance.now() - commitStart > 6)) {
      retainCodeAnchor(anchor)
      await yieldPreview(signal)
      // Take a fresh anchor after yielding so scrolling during coloring remains
      // the user's choice, rather than restoring an old whole-render position.
      anchor = codeReadingAnchor(block); committed = 0; commitStart = performance.now()
    }
  }
  retainCodeAnchor(anchor)
  signal?.throwIfAborted()
  block.colored = undefined; block.coloredLines = undefined; block.coloredChunk = undefined
  block.complete = true; block.html = undefined
  block.wrapper.dataset.highlightState = 'complete'
}

function escapeText(source: string) {
  return source.replace(/[&<>]/g, value => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[value]!)
}

export class MarkdownPreview {
  private blocks: Block[] = []

  constructor(private root: HTMLElement) {}

  async render(source: string, options: MarkdownRenderOptions = {}) {
    const signal = options.signal
    signal?.throwIfAborted()
    const parser = createMarkdownParser(options)
    // Lex the complete document so reference definitions in later blocks still
    // affect earlier links. A changed definition invalidates their cache too.
    const tokens = parser.lexer(source)
    const context = JSON.stringify([tokens.links, options.theme, options.themeId, options.preferences,
      options.citationNumbers, options.citationAliases])
    const available = new Map<string, Block[]>()
    for (const block of this.blocks) {
      const matches = available.get(block.key) ?? []
      matches.push(block); available.set(block.key, matches)
    }
    const next: Block[] = []
    for (const token of tokens) {
      if (token.type === 'space') continue
      const language = String(('lang' in token ? token.lang : '') || 'text').toLowerCase().split(/\s+/)[0]
      // Ordinary code is literal text with both palettes already embedded.
      // Citations, link definitions and CSS-only theme/wrap changes cannot
      // invalidate it. Diagram/math fences still use their rendering context.
      const literalCode = token.type === 'code' && !['mermaid', 'function-plot', 'function_plot', 'latex'].includes(language!)
      const key = (literalCode ? '' : context) + '\0' + token.type + '\0' + token.raw
      const previous = available.get(key)?.shift()
      if (previous) { next.push(previous); continue }
      const deferred: { node: HTMLElement; source: string; language: string }[] = []
      const fragment = await renderMarkdownFragment(parser.parser([token]), options, (source, language) => {
        const node = document.createElement('div')
        deferred.push({ node, source, language }); return node
      })
      const codes: CodeBlock[] = []
      for (const item of deferred) {
        // Only a restored, generated slot may be filled; sanitation that
        // removes an ancestor cannot make us replace a user-selected node.
        if (!fragment.contains(item.node)) continue
        const code = await createCode(item.source, item.language, signal)
        item.node.replaceWith(code.wrapper); codes.push(code)
      }
      next.push({ key, nodes: [...fragment.childNodes], codes })
    }
    signal?.throwIfAborted()
    // Touch only changed blocks. Stable nodes retain selection, scroll, folded
    // callouts, diagram zoom/source state and already computed code colors.
    let cursor = this.root.firstChild
    for (const block of next) for (const node of block.nodes) {
      if (node === cursor) cursor = cursor.nextSibling
      else this.root.insertBefore(node, cursor)
    }
    while (cursor) { const removed = cursor; cursor = cursor.nextSibling; this.root.removeChild(removed) }
    this.blocks = next
    if (!options.previewOnly) for (const block of next) for (const code of block.codes) await colorCode(code, signal)
  }

  dispose() { this.blocks = [] }
}
