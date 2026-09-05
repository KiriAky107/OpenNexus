import { LanguageDescription, LanguageSupport, StreamLanguage } from '@codemirror/language'
import { Decoration, EditorView, ViewPlugin, type DecorationSet, type ViewUpdate } from '@codemirror/view'
import { bundledLanguagesInfo } from 'shiki/langs'
import { getCodeTokenizer } from '@/utils/markdown'

type CodeTheme = 'github-light' | 'github-dark'

export async function shikiLanguage(language: string, theme: CodeTheme): Promise<LanguageSupport> {
  const tokenize = await getCodeTokenizer(theme, language)
  const highlights = ViewPlugin.fromClass(class {
    decorations: DecorationSet

    constructor(view: EditorView) { this.decorations = this.highlight(view) }

    update(update: ViewUpdate) {
      if (update.docChanged) this.decorations = this.highlight(update.view)
    }

    highlight(view: EditorView): DecorationSet {
      const tokens = tokenize(view.state.doc.toString(), language)
      const ranges = tokens.flatMap((line, index) => {
        let offset = view.state.doc.line(index + 1).from
        return line.flatMap(token => {
          const from = offset
          offset += token.content.length
          if (from === offset) return []
          const fontStyle = token.fontStyle ?? 0
          return [Decoration.mark({
            class: 'shiki-token',
            attributes: { style: `color:${token.color};font-style:${fontStyle & 1 ? 'italic' : 'normal'};font-weight:${fontStyle & 2 ? 'bold' : 'normal'};text-decoration:${fontStyle & 4 ? 'underline' : 'none'}` },
          }).range(from, offset)]
        })
      })
      return Decoration.set(ranges)
    }
  }, { decorations: value => value.decorations })

  // CodeMirror still owns selection, input and undo. Shiki owns token colors.
  const parser = StreamLanguage.define({ token(stream) { stream.skipToEnd(); return null } })
  return new LanguageSupport(parser, highlights)
}

export function shikiLanguages(theme: CodeTheme): LanguageDescription[] {
  return [
    ...bundledLanguagesInfo.map(info => LanguageDescription.of({
      name: info.id,
      alias: [info.name, ...(info.aliases ?? [])],
      load: () => shikiLanguage(info.id, theme),
    })),
    LanguageDescription.of({ name: 'text', alias: ['Plain text', 'txt', 'plaintext'], load: () => shikiLanguage('text', theme) }),
  ]
}
const languageLabels = new Map(bundledLanguagesInfo.flatMap(info =>
  [info.id, info.name, ...(info.aliases ?? [])].map(alias => [alias.toLowerCase(), info.name] as const),
))
export function renderCodeLanguage(language: string): string {
  return languageLabels.get(language.toLowerCase()) ?? (['text', 'txt', 'plaintext'].includes(language.toLowerCase()) ? 'Plain text' : language)
}
export function shikiEditorTheme(theme: CodeTheme) {
  return EditorView.theme({
    '&': { color: 'var(--color-code-text)', backgroundColor: 'var(--color-code-background)' },
    '.cm-gutters': { color: 'var(--color-code-muted)', backgroundColor: 'var(--color-code-background)' },
  }, { dark: theme === 'github-dark' })
}
