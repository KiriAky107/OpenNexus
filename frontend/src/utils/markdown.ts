import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { createHighlighterCore } from 'shiki/core'
import { createOnigurumaEngine } from 'shiki/engine/oniguruma'
import { bundledLanguagesInfo } from 'shiki/langs'
import githubDark from '@shikijs/themes/github-dark'
import githubLight from '@shikijs/themes/github-light'
import { renderMermaid } from '@/services/mermaidService'
import { appendDiagramControls } from './diagramControls'
import katex from 'katex'
import 'katex/dist/katex.min.css'

function mathHtml(source: string, displayMode: boolean) {
  const result = katex.renderToString(source, {displayMode, throwOnError:false, trust:false, maxExpand:1000, output:'html'})
  const label = source.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
  return `<${displayMode ? 'div' : 'span'} class="markdown-math" role="math" aria-label="${label}">${result}</${displayMode ? 'div' : 'span'}>`
}

marked.use({extensions:[
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

// Highlighter 是昂贵的单例；复用初始化 Promise，避免每个代码块重复加载语法与主题。
const highlighter = createHighlighterCore({
  themes: [githubLight, githubDark],
  langs: [],
  engine: createOnigurumaEngine(import('shiki/wasm')),
})

const languageAliases = new Map(bundledLanguagesInfo.flatMap(info =>
  [info.id, info.name, ...(info.aliases ?? [])].map(alias => [alias.toLowerCase(), info.id] as const),
))
const languageLoads = new Map<string, Promise<void>>()
const languageLoaders = new Map(bundledLanguagesInfo.map(info => [info.id, info.import]))

async function loadCodeLanguage(requestedLanguage: string) {
  const shiki = await highlighter
  const language = languageAliases.get(requestedLanguage.toLowerCase())
  if (!language) return { shiki, language: 'text' as const }
  let loading = languageLoads.get(language)
  if (!loading) {
    loading = shiki.loadLanguage(languageLoaders.get(language)!).catch(error => {
      languageLoads.delete(language)
      throw error
    })
    languageLoads.set(language, loading)
  }
  await loading
  return { shiki, language }
}

export async function highlightCode(source: string, requestedLanguage = 'text'): Promise<string> {
  const { shiki, language } = await loadCodeLanguage(requestedLanguage)
  return shiki.codeToHtml(source, {
    lang: language,
    themes: { light: 'github-light', dark: 'github-dark' },
    defaultColor: false,
  })
}

/** Share the initialized grammar/theme registry with editable code blocks. */
export async function getCodeTokenizer(theme: 'github-light' | 'github-dark', requestedLanguage = 'text') {
  const { shiki } = await loadCodeLanguage(requestedLanguage)
  return (source: string, requestedLanguage: string) => {
    const language = languageAliases.get(requestedLanguage.toLowerCase()) ?? 'text'
    return shiki.codeToTokens(source, {
      lang: shiki.getLoadedLanguages().includes(language as never) ? language : 'text',
      theme,
    }).tokens
  }
}

export async function renderMarkdown(source: string, options?: { theme?: 'light' | 'dark' }): Promise<string> {
  const html = marked.parse(source, { async: false }) as string
  const documentNode = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html')

  const mermaidBlocks: { pre: Element; source: string }[] = []

  for (const code of documentNode.querySelectorAll('pre > code')) {
    const requestedLanguage = [...code.classList].find((name) => name.startsWith('language-'))?.slice(9) || 'text'
    if (requestedLanguage === 'mermaid') {
      mermaidBlocks.push({ pre: code.parentElement!, source: code.textContent ?? '' })
      continue
    }
    if (requestedLanguage.toLowerCase() === 'latex') {
      code.parentElement?.replaceWith(document.createRange().createContextualFragment(mathHtml(code.textContent ?? '', true)))
      continue
    }
    const highlighted = await highlightCode(code.textContent ?? '', requestedLanguage)
    const fragment = document.createRange().createContextualFragment(highlighted)
    code.parentElement?.replaceWith(fragment)
  }

  for (const { pre, source } of mermaidBlocks) {
    try {
      const result = await renderMermaid(source, { theme: options?.theme, mode: 'static' })
      const container = document.createElement('div')
      container.className = 'markdown-mermaid'
      container.innerHTML = result.svg
      if (!result.warnings.length) appendDiagramControls(container)
      pre.replaceWith(container)
    } catch {
      const fallback = document.createElement('pre')
      fallback.className = 'mermaid-error'
      fallback.textContent = source
      pre.replaceWith(fallback)
    }
  }

  return DOMPurify.sanitize(documentNode.body.innerHTML, {
    USE_PROFILES: { html: true },
    ADD_TAGS: ['svg', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
      'text', 'tspan', 'textPath', 'g', 'defs', 'marker', 'style', 'clipPath', 'foreignObject',
      'title', 'desc', 'use', 'image', 'linearGradient', 'stop', 'radialGradient'],
    ADD_ATTR: ['viewBox', 'd', 'cx', 'cy', 'r', 'rx', 'ry', 'x', 'y', 'width', 'height',
      'fill', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-linecap', 'stroke-linejoin',
      'transform', 'points', 'x1', 'y1', 'x2', 'y2', 'class', 'id', 'style', 'text-anchor',
      'dominant-baseline', 'font-size', 'font-family', 'font-weight', 'opacity', 'orient',
      'marker-end', 'marker-start', 'marker-mid', 'refX', 'refY', 'viewBox', 'preserveAspectRatio',
      'xlink:href', 'href', 'clip-path', 'gradientUnits', 'gradientTransform', 'stop-color',
      'stop-opacity', 'offset', 'patternUnits', 'patternTransform', 'target'],
  })
}

// TODO(performance): 编辑器首屏稳定后评估将 Shiki 延迟加载或迁移到 Web Worker。
