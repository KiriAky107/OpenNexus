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

marked.setOptions({ gfm: true, breaks: true })

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

export async function renderMarkdown(source: string): Promise<string> {
  const html = marked.parse(source, { async: false }) as string
  const documentNode = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html')
  for (const code of documentNode.querySelectorAll('pre > code')) {
    const requestedLanguage = [...code.classList].find((name) => name.startsWith('language-'))?.slice(9) || 'text'
    const highlighted = await highlightCode(code.textContent ?? '', requestedLanguage)
    const fragment = document.createRange().createContextualFragment(highlighted)
    code.parentElement?.replaceWith(fragment)
  }

  return DOMPurify.sanitize(documentNode.body.innerHTML, { USE_PROFILES: { html: true } })
}
