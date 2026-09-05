import { LanguageDescription, LanguageSupport, StreamLanguage } from '@codemirror/language'
import { Decoration, EditorView, ViewPlugin, type DecorationSet, type ViewUpdate } from '@codemirror/view'
import { getCodeTokenizer } from '@/utils/markdown'

type CodeTheme = 'github-light' | 'github-dark'

export async function shikiLanguage(language: string, theme: CodeTheme): Promise<LanguageSupport> {
  const tokenize = await getCodeTokenizer(theme)
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

export function shikiLanguages(theme: CodeTheme, originalLanguages: readonly LanguageDescription[] = []): LanguageDescription[] {
  const overrides = [
    { name: 'C', alias: ['c'] },
    { name: 'C++', alias: ['cpp', 'c++'] },
    { name: 'Python', alias: ['python', 'py'] },
    { name: 'JavaScript', alias: ['javascript', 'js'] },
    { name: 'TypeScript', alias: ['typescript', 'ts'] },
    { name: 'HTML', alias: ['html'] },
    { name: 'CSS', alias: ['css'] },
    { name: 'JSON', alias: ['json'] },
    { name: 'Shell', alias: ['shell', 'bash', 'sh'] },
    { name: 'SQL', alias: ['sql'] },
    { name: 'Markdown', alias: ['markdown', 'md'] },
    { name: 'Plain text', alias: ['text', 'plaintext'] },
    { name: 'LaTeX', alias: ['latex'] },
  ].map(({ name, alias }) => LanguageDescription.of({
    name, alias, load: () => shikiLanguage(alias[0]!, theme),
  }))
  // Keep Crepe's full registry and lazy loaders for languages without Shiki grammars.
  const remaining = new Map(overrides.map(language => [language.name.toLowerCase(), language]))
  const languages = originalLanguages.map(original => {
    const key = original.name.toLowerCase()
    const replacement = remaining.get(key)
    if (!replacement) return original
    remaining.delete(key)
    return LanguageDescription.of({
      name: original.name,
      alias: [...new Set([...original.alias, ...replacement.alias])],
      extensions: original.extensions,
      filename: original.filename,
      load: () => replacement.load(),
    })
  })
  return [...languages, ...remaining.values()]
}

export function shikiEditorTheme(theme: CodeTheme) {
  return EditorView.theme({
    '&': { color: 'var(--color-code-text)', backgroundColor: 'var(--color-code-background)' },
    '.cm-gutters': { color: 'var(--color-code-muted)', backgroundColor: 'var(--color-code-background)' },
  }, { dark: theme === 'github-dark' })
}
