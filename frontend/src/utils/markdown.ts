import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { createHighlighterCore } from 'shiki/core'
import { createOnigurumaEngine } from 'shiki/engine/oniguruma'
import { bundledLanguagesInfo } from 'shiki/langs'
import githubDark from '@shikijs/themes/github-dark'
import githubLight from '@shikijs/themes/github-light'

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

export async function renderMarkdown(source: string): Promise<string> {
  const html = marked.parse(source, { async: false }) as string
  const documentNode = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html')
  for (const code of documentNode.querySelectorAll('pre > code')) {
    const requestedLanguage = [...code.classList].find((name) => name.startsWith('language-'))?.slice(9) || 'text'
    const highlighted = await highlightCode(code.textContent ?? '', requestedLanguage)
    const fragment = document.createRange().createContextualFragment(highlighted)
    code.parentElement?.replaceWith(fragment)
  }

  // Markdown 可能来自模型或外部笔记，高亮完成后仍必须在最终出口统一净化。
  return DOMPurify.sanitize(documentNode.body.innerHTML, { USE_PROFILES: { html: true } })
}

// TODO(performance): 编辑器首屏稳定后评估将 Shiki 延迟加载或迁移到 Web Worker。
