import { EditorState, MapMode, StateEffect, StateField, type ChangeDesc, type Extension } from '@codemirror/state'
import { invertedEffects } from '@codemirror/commands'

type Break = '\n' | '\r\n' | '\r'
interface Ending { pos: number; ending: Break }
interface Format { preferred: Break; alternate: readonly Ending[] }

function mapEndings(value: readonly Ending[], changes: ChangeDesc): Ending[] {
  return value.flatMap(mark => {
    const pos = changes.mapPos(mark.pos, 1, MapMode.TrackDel)
    return pos === null ? [] : [{ pos, ending: mark.ending }]
  })
}

/** Keep unchanged source bytes, including mixed line endings, outside CM's normalized text model. */
export function mobileSourceText(raw: string): { doc: string; extension: Extension; read: (state: EditorState) => string; position: (state: EditorState, position: number) => number } {
  const preferred = (raw.match(/\r\n|\r|\n/)?.[0] ?? '\n') as Break
  const alternate: Ending[] = []
  let extra = 0
  for (const match of raw.matchAll(/\r\n|\r|\n/g)) {
    const pos = match.index - extra
    if (match[0] !== preferred) alternate.push({ pos, ending: match[0] as Break })
    extra += match[0].length - 1
  }
  const restore = StateEffect.define<readonly Ending[]>({
    map(value, changes) { const mapped = mapEndings(value, changes); return mapped.length ? mapped : undefined },
  })
  const field = StateField.define<Format>({
    create: () => ({ preferred, alternate }),
    update(format, transaction) {
      const mapped = transaction.docChanged ? mapEndings(format.alternate, transaction.changes) : format.alternate
      const marks = new Map(mapped.map(mark => [mark.pos, mark.ending]))
      for (const effect of transaction.effects) if (effect.is(restore)) {
        for (const mark of effect.value) {
          if (mark.pos < 0 || mark.pos >= transaction.newDoc.length || transaction.newDoc.sliceString(mark.pos, mark.pos + 1) !== '\n') {
            throw new Error('MOBILE_EDITOR_NEWLINE_STATE_INVALID')
          }
          if (mark.ending === format.preferred) marks.delete(mark.pos)
          else marks.set(mark.pos, mark.ending)
        }
      }
      return { preferred: format.preferred, alternate: [...marks].map(([pos, ending]) => ({ pos, ending })).sort((a, b) => a.pos - b.pos) }
    },
  })
  const inversion = invertedEffects.of(transaction => {
    if (!transaction.docChanged) return []
    const before = transaction.startState.field(field), marks = new Map(before.alternate.map(mark => [mark.pos, mark.ending]))
    const deleted: Ending[] = []
    transaction.changes.iterChanges((from, to) => {
      const text = transaction.startState.doc.sliceString(from, to)
      for (let index = text.indexOf('\n'); index >= 0; index = text.indexOf('\n', index + 1)) {
        const pos = from + index
        deleted.push({ pos, ending: marks.get(pos) ?? before.preferred })
      }
    })
    return deleted.length ? [restore.of(deleted)] : []
  })
  return {
    doc: raw.replace(/\r\n|\r/g, '\n'), extension: [field, inversion],
    position(state, position) {
      if (position < 0 || position > state.doc.length) throw new Error('MOBILE_SELECTION_INVALID')
      const format = state.field(field)
      return state.doc.sliceString(0, position, format.preferred).length + format.alternate
        .filter(mark => mark.pos < position).reduce((extra, mark) => extra + mark.ending.length - format.preferred.length, 0)
    },
    read(state) {
      const format = state.field(field), parts: string[] = []
      let from = 0
      for (const mark of format.alternate) {
        if (mark.pos < from || state.doc.sliceString(mark.pos, mark.pos + 1) !== '\n') throw new Error('MOBILE_EDITOR_NEWLINE_STATE_INVALID')
        parts.push(state.doc.sliceString(from, mark.pos, format.preferred), mark.ending)
        from = mark.pos + 1
      }
      parts.push(state.doc.sliceString(from, state.doc.length, format.preferred))
      return parts.join('')
    },
  }
}
