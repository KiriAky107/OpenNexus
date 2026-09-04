import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { createHighlighterCore } from 'shiki/core'
import { createJavaScriptRegexEngine } from '@shikijs/engine-javascript'
import css from '@shikijs/langs/css'
import html from '@shikijs/langs/html'
import javascript from '@shikijs/langs/javascript'
import json from '@shikijs/langs/json'
import markdown from '@shikijs/langs/markdown'
import python from '@shikijs/langs/python'
import shell from '@shikijs/langs/shellscript'
import sql from '@shikijs/langs/sql'
import typescript from '@shikijs/langs/typescript'
import githubDark from '@shikijs/themes/github-dark'
import githubLight from '@shikijs/themes/github-light'
import { renderMermaid } from '@/services/mermaidService'

marked.setOptions({ gfm: true, breaks: true })

// Highlighter 是昂贵的单例；复用初始化 Promise，避免每个代码块重复加载语法与主题。
const highlighter = createHighlighterCore({
  themes: [githubLight, githubDark],
  langs: [markdown, html, css, javascript, typescript, json, python, shell, sql],
  engine: createJavaScriptRegexEngine(),
})

const languageAliases: Record<string, string> = {
  bash: 'shell', js: 'javascript', md: 'markdown', plaintext: 'text', py: 'python', sh: 'shell', ts: 'typescript',
}

export async function highlightCode(source: string, requestedLanguage = 'text'): Promise<string> {
  const shiki = await highlighter
  const language = languageAliases[requestedLanguage] ?? requestedLanguage
  const loadedLanguage = shiki.getLoadedLanguages().includes(language as never) ? language : 'markdown'
  return shiki.codeToHtml(source, {
    lang: loadedLanguage,
    themes: { light: 'github-light', dark: 'github-dark' },
    defaultColor: false,
  })
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
