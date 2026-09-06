import { $prose } from '@milkdown/kit/utils'
import { Plugin } from '@milkdown/kit/prose/state'
import type { EditorView } from '@milkdown/kit/prose/view'

function reconcile(view: EditorView) {
  if (view.isDestroyed || view.composing || !view.state.selection.empty) return
  const { $from } = view.state.selection
  if (!$from.parent.isTextblock || $from.parent.type.spec.code) return
  const text = $from.parent.textBetween(0, $from.parent.content.size, '\n', '\ufffc')
  // Also inspect the closing delimiter AFTER the caret: users commonly type
  // a pair of backticks first, move left, and then fill in the code.
  const spans = /(^|[^\\`])`([^`\n\ufffc]+)`(?!`)/g
  let candidate: { start: number; end: number } | undefined
  for (const match of text.matchAll(spans)) {
    const start = match.index! + match[1]!.length
    const end = match.index! + match[0].length
    if (match[2]!.trim() && $from.parentOffset > start && $from.parentOffset <= end) {
      candidate = { start: $from.start() + start, end: $from.start() + end }
      break
    }
  }
  if (!candidate) return
  const mark = view.state.schema.marks.inlineCode
  if (!mark) return
  const { start, end } = candidate
  if (view.state.doc.rangeHasMark(start, end, mark)) return
  const tr = view.state.tr.delete(end - 1, end).delete(start, start + 1)
  tr.removeMark(start, end - 2).addMark(start, end - 2, mark.create())
  // Filling an existing pair must keep subsequent letters inside code. Typing
  // the closing delimiter explicitly should instead leave code as usual.
  if ($from.pos < end) tr.setStoredMarks([mark.create()])
  else tr.removeStoredMark(mark)
  view.dispatch(tr)
}

// DOM input can bypass handleTextInput (IME, replacement text, missing event.data).
// Observe it in capture phase, then wait for ProseMirror's DOM observer and its
// composition cleanup before inspecting the document. Never reconcile on load,
// paste, undo, or a selection change alone.
export const inlineCodeInputPlugin = $prose(() => new Plugin({
  view(view) {
    let timer: ReturnType<typeof setTimeout> | undefined
    let pending = false
    let composing = false
    let attempts = 0
    const cancel = () => { clearTimeout(timer); pending = false }
    const schedule = () => {
      clearTimeout(timer)
      if (!pending || composing) return
      timer = setTimeout(() => {
        if (view.isDestroyed) return cancel()
        if (view.composing) {
          if (++attempts < 10) schedule()
          else cancel()
          return
        }
        pending = false
        reconcile(view)
      }, 30)
    }
    const input = (event: Event) => {
      const type = (event as InputEvent).inputType
      if (type && (!type.startsWith('insert') || /paste|drop/i.test(type))) return cancel()
      pending = true
      attempts = 0
      schedule()
    }
    const start = () => { composing = true; cancel() }
    const end = () => { composing = false; pending = true; attempts = 0; schedule() }
    const keydown = (event: KeyboardEvent) => {
      // ProseMirror handles undo/paste itself, so those actions need not emit input.
      if (event.ctrlKey || event.metaKey || ['Backspace', 'Delete', 'Escape'].includes(event.key)) cancel()
      else if (pending && !composing && !view.composing && event.key === 'Enter') {
        cancel()
        reconcile(view)
      }
    }
    view.dom.addEventListener('input', input, true)
    view.dom.addEventListener('keydown', keydown, true)
    view.dom.addEventListener('paste', cancel, true)
    view.dom.addEventListener('drop', cancel, true)
    view.dom.addEventListener('compositionstart', start, true)
    view.dom.addEventListener('compositionend', end, true)
    return {
      update(current, previous) {
        if (pending && !current.state.doc.eq(previous.doc)) schedule()
      },
      destroy() {
        cancel()
        view.dom.removeEventListener('input', input, true)
        view.dom.removeEventListener('keydown', keydown, true)
        view.dom.removeEventListener('paste', cancel, true)
        view.dom.removeEventListener('drop', cancel, true)
        view.dom.removeEventListener('compositionstart', start, true)
        view.dom.removeEventListener('compositionend', end, true)
      },
    }
  },
}))
