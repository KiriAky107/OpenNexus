import { LanguageDescription, LanguageSupport, StreamLanguage } from '@codemirror/language'
import { StateEffect, Transaction, type Text } from '@codemirror/state'
import { Decoration, EditorView, ViewPlugin, type DecorationSet, type ViewUpdate } from '@codemirror/view'
import { bundledLanguagesInfo } from 'shiki/langs'
import type { CodeTheme } from '@/services/codeHighlighter'
import { codeHighlights } from './codeHighlightClient'
import { MAX_HIGHLIGHT_CHARACTERS, type HighlightResult } from './codeHighlightProtocol'

const highlighted = StateEffect.define<{ owner: object; document: Text; result?: HighlightResult }>()

function visibleDecorations(view: EditorView, result: HighlightResult): DecorationSet {
  const { spans, styles } = result
  const marks = styles.map(style => Decoration.mark({ class: 'shiki-token', attributes: { style } }))
  const ranges = []
  for (const visible of view.visibleRanges) {
    // Binary search avoids scanning off-screen tokens on scroll or result arrival.
    let lo = 0, hi = spans.length / 3
    while (lo < hi) { const mid = (lo + hi) >>> 1; if (spans[mid * 3 + 1]! <= visible.from) lo = mid + 1; else hi = mid }
    for (let index = lo * 3; index < spans.length && spans[index]! < visible.to; index += 3) {
      const from = Math.max(visible.from, spans[index]!), to = Math.min(visible.to, spans[index + 1]!)
      if (from < to) ranges.push(marks[spans[index + 2]!]!.range(from, to))
    }
  }
  return Decoration.set(ranges, true)
}

export async function shikiLanguage(language: string, theme: CodeTheme): Promise<LanguageSupport> {
  const highlights = ViewPlugin.fromClass(class {
    decorations: DecorationSet = Decoration.none
    private revision = 0
    private disposed = false
    private timer?: ReturnType<typeof setTimeout>
    private result?: { document: Text; value: HighlightResult }

    constructor(private view: EditorView) { this.schedule(0) }

    update(update: ViewUpdate) {
      if (update.docChanged) {
        this.revision++
        this.result = undefined
        this.decorations = this.decorations.map(update.changes)
        this.schedule(30)
      }
      for (const transaction of update.transactions) for (const effect of transaction.effects) {
        if (effect.is(highlighted) && effect.value.owner === this && effect.value.document === update.state.doc) {
          this.result = effect.value.result ? { document: effect.value.document, value: effect.value.result } : undefined
          this.decorations = this.result ? visibleDecorations(update.view, this.result.value) : Decoration.none
        }
      }
      if (update.viewportChanged && this.result?.document === update.state.doc) {
        this.decorations = visibleDecorations(update.view, this.result.value)
      }
    }

    private schedule(delay: number) {
      clearTimeout(this.timer)
      codeHighlights.cancel(this)
      // Do not flatten the document or clone/tokenize it in an input transaction.
      this.timer = setTimeout(() => {
        if (this.disposed) return
        const document = this.view.state.doc, revision = this.revision
        const receive = (result?: HighlightResult) => {
          if (this.disposed || this.revision !== revision || this.view.state.doc !== document) return
          this.view.dispatch({ effects: highlighted.of({ owner: this, document, result }), annotations: Transaction.addToHistory.of(false) })
        }
        if (document.length > MAX_HIGHLIGHT_CHARACTERS) { receive(); return }
        codeHighlights.request(this, { source: document.toString(), language, theme }, receive)
      }, delay)
    }

    destroy() {
      this.disposed = true
      clearTimeout(this.timer)
      codeHighlights.cancel(this)
      this.result = undefined
    }
  }, { decorations: value => value.decorations })

  // CodeMirror仍然拥有选择、输入和撤消功能。 Shiki拥有令牌颜色。
  const parser = StreamLanguage.define({ token(stream) { stream.skipToEnd(); return null } })
  return new LanguageSupport(parser, highlights)
}

export function shikiLanguages(theme: CodeTheme): LanguageDescription[] {
  return [
    LanguageDescription.of({ name: 'function-plot', alias: ['Function Plot'], load: () => shikiLanguage('text', theme) }),
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
