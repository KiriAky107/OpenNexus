import { $prose } from '@milkdown/kit/utils'
import { Plugin } from '@milkdown/kit/prose/state'
import type { EditorView } from '@milkdown/kit/prose/view'

function reconcile(view: EditorView) {
  if (view.isDestroyed || view.composing || !view.state.selection.empty) return
  const { $from } = view.state.selection
  if (!$from.parent.isTextblock || $from.parent.type.spec.code) return
  const text = $from.parent.textBetween(0, $from.parent.content.size, '\n', '\ufffc')
  // 还要检查结束分隔符 AFTER 插入符号：用户通常首先键入一对反引号，向左移动，然后填写代码。
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
// 补全已有成对标记时，后续字母必须留在行内代码中；显式输入结束标记则应像通常一样离开代码范围。
  if ($from.pos < end) tr.setStoredMarks([mark.create()])
  else tr.removeStoredMark(mark)
  view.dispatch(tr)
}

// DOM 输入可能绕过 handleTextInput（输入法、替换文本或缺少 event.data）。
// 在捕获阶段观察输入，等待 ProseMirror 的 DOM 观察器和组合输入清理完成后再检查文档。
// 加载、粘贴、撤销或仅改变选区时不执行协调。
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
// ProseMirror 会自行处理撤销和粘贴，因此这些操作无需触发 input。
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
