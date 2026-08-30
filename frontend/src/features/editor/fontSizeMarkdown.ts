import { $prose } from '@milkdown/kit/utils'
import { Plugin, TextSelection } from '@milkdown/kit/prose/state'
import { Decoration, DecorationSet } from '@milkdown/kit/prose/view'
import type { Editor } from '@milkdown/kit/core'
import { editorViewCtx } from '@milkdown/kit/core'

const openingTag = /^<span style="font-size:\s*(\d+(?:\.\d+)?)px">$/i
const closingTag = /^<\/span>$/i

export const fontSizeMarkdownPlugin = $prose(() => new Plugin({
  props: {
    decorations(state) {
      const decorations: Decoration[] = []
      const stack: Array<{ from: number; size: string }> = []

      state.doc.descendants((node, position) => {
        if (node.type.name !== 'html') return
        const value = String(node.attrs.value ?? '')
        const match = value.match(openingTag)

        if (match) {
          stack.push({ from: position + node.nodeSize, size: match[1] })
          decorations.push(Decoration.node(position, position + node.nodeSize, { class: 'font-size-marker' }))
          return
        }

        if (closingTag.test(value)) {
          const opening = stack.pop()
          decorations.push(Decoration.node(position, position + node.nodeSize, { class: 'font-size-marker' }))
          if (opening && opening.from < position) {
            decorations.push(Decoration.inline(opening.from, position, {
              style: `font-size: ${opening.size}px`,
              'data-font-size': opening.size,
            }))
          }
        }
      })

      return DecorationSet.create(state.doc, decorations)
    },
  },
}))

export function applyMarkdownFontSize(editor: Editor, size: number): boolean {
  return editor.action((ctx) => {
    const view = ctx.get(editorViewCtx)
    const { from, to, empty } = view.state.selection
    if (empty) return false

    const htmlNode = view.state.schema.nodes.html
    if (!htmlNode) return false

    const opening = htmlNode.create({ value: `<span style="font-size: ${size}px">` })
    const closing = htmlNode.create({ value: '</span>' })
    const transaction = view.state.tr
      .insert(to, closing)
      .insert(from, opening)
    transaction.setSelection(TextSelection.create(transaction.doc, from + 1, to + 1))

    view.dispatch(transaction)
    view.focus()
    return true
  })
}
