<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'
import { Transaction } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { redo, undo } from '@codemirror/commands'
import SourceEditorSurface from '../../src/features/editor/SourceEditorSurface.vue'
import { mobileSourceText } from './sourceText'

const props = defineProps<{ modelValue: string; label: string; language: string; readOnly?: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [text: string]; compositionStart: []; compositionEnd: []; error: [code: string]
}>()
let view: EditorView | undefined
const source = mobileSourceText(props.modelValue)
const surfaceExtensions = [source.extension,
    EditorView.updateListener.of(update => {
      if (!update.docChanged) return
      try { emit('update:modelValue', source.read(update.state)) }
      catch (error) { emit('error', error instanceof Error ? error.message : 'MOBILE_EDITOR_FORMAT_ERROR') }
    }),
]
function ready(currentView: EditorView) { view = currentView }
watch(() => props.modelValue, value => {
  if (!view || source.read(view.state) === value) return
  // A new document is mounted with a new editor-session key. Unexpected foreign text cannot replace dirty input.
  emit('error', 'MOBILE_EDITOR_CONTENT_CHANGED')
})
onBeforeUnmount(() => { view = undefined })
defineExpose({
  undo: () => view && !props.readOnly && !view.compositionStarted && undo(view),
  redo: () => view && !props.readOnly && !view.compositionStarted && redo(view),
  insert(text: string) {
    if (!view || view.compositionStarted || props.readOnly) return false
    view.dispatch(view.state.replaceSelection(text)); view.focus(); return true
  },
  focus: () => view?.focus(),
  selectedText: () => view?.state.sliceDoc(view.state.selection.main.from, view.state.selection.main.to) ?? '',
  blur: () => view?.contentDOM.blur(),
  selection: () => view ? { from: source.position(view.state, view.state.selection.main.from), to: source.position(view.state, view.state.selection.main.to) } : null,
  acceptImage(before: string, after: string) {
    if (!view || view.compositionStarted || source.read(view.state) !== before) return false
    let from = 0, oldEnd = before.length, newEnd = after.length
    while (from < oldEnd && from < newEnd && before[from] === after[from]) from++
    while (oldEnd > from && newEnd > from && before[oldEnd-1] === after[newEnd-1]) { oldEnd--; newEnd-- }
    const cmPosition = (raw: number) => before.slice(0, raw).replace(/\r\n|\r/g, '\n').length
    view.dispatch({ changes: { from: cmPosition(from), to: cmPosition(oldEnd), insert: after.slice(from, newEnd) }, annotations: Transaction.userEvent.of('input.image') })
    return source.read(view.state) === after
  },
})
</script>

<template><SourceEditorSurface class="mobile-editor" :initial-content="source.doc" :extensions="surfaceExtensions" :label="label" :language="language" :read-only="readOnly" :line-numbers="false" touch @ready="ready" @composition-start="emit('compositionStart')" @composition-end="emit('compositionEnd')" /></template>
