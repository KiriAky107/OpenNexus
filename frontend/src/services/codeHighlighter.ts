// No DOM, Vue or Markdown renderer dependencies: shared by previews and Worker.
import { createHighlighterCore } from 'shiki/core'
import { createOnigurumaEngine } from 'shiki/engine/oniguruma'
import { bundledLanguagesInfo } from 'shiki/langs'
import githubDark from '@shikijs/themes/github-dark'
import githubLight from '@shikijs/themes/github-light'

export type CodeTheme = 'github-light' | 'github-dark'
let highlighter: ReturnType<typeof createHighlighterCore> | undefined
function getHighlighter() { return highlighter ??= createHighlighterCore({
  themes: [githubLight, githubDark], langs: [],
  engine: createOnigurumaEngine(import('shiki/wasm')),
}).catch(error => { highlighter = undefined; throw error }) }

const languageAliases = new Map(bundledLanguagesInfo.flatMap(info =>
  [info.id, info.name, ...(info.aliases ?? [])].map(alias => [alias.toLowerCase(), info.id] as const),
))
const languageLoads = new Map<string, Promise<void>>()
const languageLoaders = new Map(bundledLanguagesInfo.map(info => [info.id, info.import]))

export async function loadCodeLanguage(requestedLanguage: string) {
  const shiki = await getHighlighter()
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

export async function getCodeTokenizer(theme: CodeTheme, requestedLanguage = 'text') {
  const { shiki } = await loadCodeLanguage(requestedLanguage)
  return (source: string, requestedLanguage: string) => {
    const language = languageAliases.get(requestedLanguage.toLowerCase()) ?? 'text'
    return shiki.codeToTokens(source, {
      lang: shiki.getLoadedLanguages().includes(language as never) ? language : 'text', theme,
    }).tokens
  }
}
