<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Compartment, EditorState, Transaction } from '@codemirror/state'
import { EditorView, keymap } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, redo, undo } from '@codemirror/commands'
import { markdown } from '@codemirror/lang-markdown'
import { mobileSourceText } from './sourceText'

const props = defineProps<{ modelValue: string; label: string; language: string; readOnly?: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [text: string]; compositionStart: []; compositionEnd: []; error: [code: string]
}>()
const host = ref<HTMLElement>()
let view: EditorView | undefined, source = mobileSourceText(props.modelValue), disposed = false, composition = 0
let compositionTimer: ReturnType<typeof setTimeout> | undefined
const attributes = new Compartment(), editable = new Compartment()
function contentAttributes() {
  return EditorView.contentAttributes.of({ 'aria-label': props.label, lang: props.language, spellcheck: 'true', autocapitalize: 'sentences' })
}
function finishComposition(token: number) {
  if (disposed || token !== composition || !view) return
  if (view.compositionStarted) {
    compositionTimer = setTimeout(() => finishComposition(token), 16)
  } else {
    compositionTimer = setTimeout(() => { if (!disposed && token === composition) emit('compositionEnd') }, 16)
  }
}
onMounted(() => {
  view = new EditorView({ parent: host.value, state: EditorState.create({ doc: source.doc, extensions: [
    source.extension, history(), keymap.of([...defaultKeymap, ...historyKeymap]), markdown(), EditorView.lineWrapping,
    attributes.of(contentAttributes()), editable.of([EditorView.editable.of(!props.readOnly), EditorState.readOnly.of(Boolean(props.readOnly))]),
    EditorView.domEventHandlers({
      compositionstart() { composition++; if (compositionTimer) clearTimeout(compositionTimer); emit('compositionStart'); return false },
      compositionend() { finishComposition(++composition); return false },
    }),
    EditorView.updateListener.of(update => {
      if (!update.docChanged) return
      try { emit('update:modelValue', source.read(update.state)) }
      catch (error) { emit('error', error instanceof Error ? error.message : 'MOBILE_EDITOR_FORMAT_ERROR') }
    }),
    EditorView.theme({
      '&': { height: '100%', fontSize: '16px' }, '.cm-scroller': { overflow: 'auto', fontFamily: 'ui-monospace, monospace', lineHeight: '1.7' },
      '.cm-content': { padding: '18px 16px 90px', minHeight: '100%' }, '&.cm-focused': { outline: 'none' },
      '.cm-line': { padding: '0' }, '.cm-cursor': { borderLeftColor: '#234d43' }, '.cm-selectionBackground': { background: '#d4e7dc !important' },
    }),
  ] }) })
})
watch(() => [props.label, props.language, props.readOnly] as const, () => {
  view?.dispatch({ effects: [attributes.reconfigure(contentAttributes()), editable.reconfigure([EditorView.editable.of(!props.readOnly), EditorState.readOnly.of(Boolean(props.readOnly))])], annotations: Transaction.addToHistory.of(false) })
})
watch(() => props.modelValue, value => {
  if (!view || source.read(view.state) === value) return
  // A new document is mounted with a new editor-session key. Unexpected foreign text cannot replace dirty input.
  emit('error', 'MOBILE_EDITOR_CONTENT_CHANGED')
})
onBeforeUnmount(() => { disposed = true; composition++; if (compositionTimer) clearTimeout(compositionTimer); view?.destroy(); view = undefined })
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
})
</script>

<template><div ref="host" class="mobile-editor" /></template>
