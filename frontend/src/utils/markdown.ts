import { renderFunctionPlot } from '@/services/functionPlotService'
import DOMPurify from 'dompurify'
import { Marked } from 'marked'
import { defaultMarkdownPreferences, type MarkdownPreferences } from '@/stores/markdownPreferences'
import { previewHighlighter } from '@/services/previewHighlighter'
import { plainCode } from '@/services/previewHighlightProtocol'
export { getCodeTokenizer } from '@/services/codeHighlighter'
import { renderMermaid, type mermaidThemeVariables } from '@/services/mermaidService'
import { appendDiagramControls } from './diagramControls'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import { parseCallout, escapeCalloutTitle } from './callouts'
import '@/styles/callouts.css'

function mathHtml(source: string, displayMode: boolean) {
  const result = katex.renderToString(source, {displayMode, throwOnError:false, trust:false, maxExpand:1000, output:'html'})
  const label = source.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
  return `<${displayMode ? 'div' : 'span'} class="markdown-math" role="math" aria-label="${label}">${result}</${displayMode ? 'div' : 'span'}>`
}

function createMarkdownParser(preferences: MarkdownPreferences) {
const marked = new Marked()
marked.use({ renderer: { blockquote(token) {
  if (!preferences.callouts) return false
  const callout = parseCallout(token.text)
  if (!callout) return false
  const title = escapeCalloutTitle(callout.title)
  const body = marked.parse(callout.body, { async: false }) as string
  const attributes = `class="markdown-callout" data-callout="${callout.type}"`
  return callout.fold
    ? `<details ${attributes}${callout.fold === '+' ? ' open' : ''}><summary class="callout-title">${title}</summary><div class="callout-body">${body}</div></details>`
    : `<aside ${attributes}><div class="callout-title">${title}</div><div class="callout-body">${body}</div></aside>`
} } })

if (preferences.math) marked.use({extensions:[
  {name:'blockMath',level:'block',tokenizer(source) {
    const match = /^ {0,3}\$\$\s*\n?([\s\S]+?)\n?\$\$[ \t]*(?:\n|$)/.exec(source)
    if (match) return {type:'blockMath',raw:match[0],text:match[1]!.trim()}
    return undefined
  }, renderer(token) { return mathHtml(token.text, true) }},
  {name:'inlineMath',level:'inline',start(source) { return source.indexOf('$') },tokenizer(source) {
    const match = /^\$([^$\n]+?)\$(?!\$)/.exec(source)
    if (match && !/^\s|\s$/.test(match[1]!)) return {type:'inlineMath',raw:match[0],text:match[1]!}
    return undefined
  },renderer(token) { return mathHtml(token.text, false) }},
]})

marked.setOptions({ gfm: true, breaks: true })
if (!preferences.autoLinks) marked.use({ tokenizer: { url() { return undefined } } })
return marked
}

export function highlightCode(source: string, requestedLanguage = 'text', signal?: AbortSignal): Promise<string> {
  return previewHighlighter.highlight(source, requestedLanguage, signal)
}

export async function renderMarkdown(source: string, options?: { signal?: AbortSignal; previewOnly?: boolean; themeId?: string; theme?: 'light' | 'dark'; preferences?: MarkdownPreferences; pdf?: { plot: (source: string) => Promise<{svg: string; warnings: string[]}>; mermaidVariables: ReturnType<typeof mermaidThemeVariables> }; citationNumbers?: number[]; citationAliases?: Record<string, number> }): Promise<string> {
  options?.signal?.throwIfAborted()
  const preferences = options?.preferences ?? defaultMarkdownPreferences
  const marked = createMarkdownParser(preferences)
  const citations = new Set(options?.citationNumbers ?? [])
  if (citations.size) marked.use({ extensions: [{ name: 'citation', level: 'inline',
    start: text => text.indexOf('['),
    tokenizer(text) {
      const match = /^\[([1-9]\d*|cit_[A-Za-z0-9_-]+)\](?!\()/.exec(text)
      const number = match ? options?.citationAliases?.[match[1]!] ?? Number(match[1]) : 0
      if (match && citations.has(number)) return { type: 'citation', raw: match[0], number }
    },
    renderer: token => `<button type="button" class="inline-citation" data-citation-number="${token.number}" aria-label="查看来源 ${token.number}">[${token.number}]</button>`,
  }] })
  const html = marked.parse(source, { async: false }) as string
  const documentNode = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html')

  const mermaidBlocks: { pre: Element; source: string; kind: string }[] = []

  for (const code of documentNode.querySelectorAll('pre > code')) {
    const requestedLanguage = [...code.classList].find((name) => name.startsWith('language-'))?.slice(9) || 'text'
    // Mermaid 与函数图共用静态图表管线；兼容旧的 function_plot 围栏写法。
    const diagramKind = requestedLanguage.toLowerCase().split(/\s+/)[0]!.replace('function_plot','function-plot')
    if (['mermaid', 'function-plot'].includes(diagramKind) && preferences.diagrams) {
      mermaidBlocks.push({ pre: code.parentElement!, source: code.textContent ?? '', kind: diagramKind })
      continue
    }
    if (requestedLanguage.toLowerCase() === 'latex' && preferences.math) {
      code.parentElement?.replaceWith(document.createRange().createContextualFragment(mathHtml(code.textContent ?? '', true)))
      continue
    }
    const source = code.textContent ?? ''
    const highlighted = options?.previewOnly
      ? previewHighlighter.cached(source, requestedLanguage) ?? plainCode(source)
      : await highlightCode(source, requestedLanguage, options?.signal)
    options?.signal?.throwIfAborted()
    const fragment = document.createRange().createContextualFragment(highlighted)
// Shiki 用换行符分隔行 span。块布局不能把分隔符渲染成额外空行；复制时仍使用未改动的源码。
    for (const node of [...(fragment.querySelector('code')?.childNodes ?? [])]) {
      if (node.nodeType === Node.TEXT_NODE && !node.textContent?.trim()) node.remove()
    }
    const wrapper = document.createElement('div')
    wrapper.className = 'markdown-code-block'
    wrapper.dataset.languageLabel = requestedLanguage
    appendCodeToolbar(wrapper, requestedLanguage, code.textContent ?? '')
    wrapper.append(fragment)
    code.parentElement?.replaceWith(wrapper)
  }

  let plotCount = 0, plotNodes = 0
  for (const { pre, source, kind } of mermaidBlocks) {
    options?.signal?.throwIfAborted()
    try {
      // 交互预览保持数量和 AST 复杂度预算；PDF 已在隔离渲染链路中按需求解除限制。
      if (!options?.pdf && kind === 'function-plot' && ++plotCount > 16) throw new Error('函数图像数量超过 16')
      const result = kind === 'function-plot' ? (options?.pdf ? await options.pdf.plot(source) : await renderFunctionPlot(source, options?.themeId)) : await renderMermaid(source, { theme: options?.theme, mode: 'static', ...(options?.pdf ? { unlimited:true, themeVariables:options.pdf.mermaidVariables } : {}) })
      if (!options?.pdf && 'nodeCount' in result && (plotNodes += Number(result.nodeCount)) > 8000) throw new Error('函数图像累计复杂度超过 8000')
      const container = document.createElement('div')
      container.className = 'markdown-mermaid' + (kind === 'function-plot' ? ' markdown-function-plot' : '')
      container.innerHTML = result.svg
      appendCodeToolbar(container, kind, source, true)
      if (result.warnings.length) { const message = document.createElement('p'); message.textContent = result.warnings.join('\n'); message.setAttribute('role', 'status'); container.append(message) }
      if (!result.warnings.length || (kind === 'function-plot' && result.svg)) appendDiagramControls(container)
      pre.replaceWith(container)
    } catch (error) {
      const fallback = document.createElement('pre')
      fallback.className = 'mermaid-error'
      fallback.textContent = `${error instanceof Error ? error.message : '图表渲染失败'}\n${source}`
      pre.replaceWith(fallback)
    }
  }

  options?.signal?.throwIfAborted()
  return DOMPurify.sanitize(documentNode.body.innerHTML, {
    USE_PROFILES: { html: true },
    HTML_INTEGRATION_POINTS: { foreignobject: true },
    ADD_TAGS: ['svg', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
      'text', 'tspan', 'textPath', 'g', 'defs', 'marker', 'style', 'clipPath', 'foreignObject',
      'title', 'desc', 'use', 'image', 'linearGradient', 'stop', 'radialGradient'],
    ADD_ATTR: ['xmlns', 'viewBox', 'd', 'cx', 'cy', 'r', 'rx', 'ry', 'x', 'y', 'width', 'height',
      'fill', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-linecap', 'stroke-linejoin',
      'transform', 'points', 'x1', 'y1', 'x2', 'y2', 'class', 'id', 'style', 'text-anchor',
      'dominant-baseline', 'font-size', 'font-family', 'font-weight', 'opacity', 'orient',
      'marker-end', 'marker-start', 'marker-mid', 'refX', 'refY', 'viewBox', 'preserveAspectRatio',
      'xlink:href', 'href', 'clip-path', 'gradientUnits', 'gradientTransform', 'stop-color',
      'stop-opacity', 'offset', 'patternUnits', 'patternTransform', 'target'],
  })
}

function appendCodeToolbar(container: HTMLElement, language: string, source: string, diagram = false) {
  const header = document.createElement('div')
  header.className = 'markdown-code-toolbar tools'
  const label = document.createElement('span'); label.textContent = language
  header.append(label)
  for (const action of diagram ? ['source', 'copy'] : ['copy']) {
    const button = document.createElement('button')
    button.type = 'button'; button.className = 'button-secondary'
    button.dataset.codeAction = action
    button.textContent = action === 'source' ? '查看源码' : '复制'
    button.setAttribute('aria-label', action === 'source' ? '查看源码' : '复制源码')
    if (action === 'source') button.setAttribute('aria-pressed', 'false')
    header.append(button)
  }
  const raw = document.createElement('pre')
  raw.className = 'markdown-code-source'; raw.hidden = true; raw.textContent = source
  container.prepend(header)
  container.append(raw)
}

// Preview grammars load on demand; editable code blocks tokenize in a Worker.
