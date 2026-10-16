<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Compartment, EditorState, Transaction, type Extension } from '@codemirror/state'
import { EditorView, keymap, lineNumbers } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { markdown } from '@codemirror/lang-markdown'

const props = withDefaults(defineProps<{
  initialContent: string; extensions?: Extension[]; label: string; language: string; spellcheck?: boolean; readOnly?: boolean; touch?: boolean; lineNumbers?: boolean
}>(), { extensions: () => [], spellcheck: true, lineNumbers: true })
const emit = defineEmits<{ ready: [view: EditorView]; compositionStart: []; compositionEnd: [] }>()
const root = ref<HTMLElement>()
const attributes = new Compartment(), editable = new Compartment()
let view: EditorView | undefined, disposed = false, composition = 0
let compositionTimer: ReturnType<typeof setTimeout> | undefined
let editContext: EventTarget | undefined, composing = false
function startComposition() {
  if (disposed || props.readOnly) return
  composition++
  if (compositionTimer) clearTimeout(compositionTimer)
  if (!composing) { composing = true; emit('compositionStart') }
}
function endComposition() {
  if (!disposed && composing) finishComposition(++composition)
}
function bindEditContext(currentView?: EditorView) {
  const next = !props.readOnly ? (currentView?.contentDOM as (HTMLElement & { editContext?: EventTarget | null }) | undefined)?.editContext ?? undefined : undefined
  if (next === editContext) return
  editContext?.removeEventListener('compositionstart', startComposition)
  editContext?.removeEventListener('compositionend', endComposition)
  editContext = next
  editContext?.addEventListener('compositionstart', startComposition)
  editContext?.addEventListener('compositionend', endComposition)
}
function contentAttributes() {
  return EditorView.contentAttributes.of({ 'aria-label': props.label, lang: props.language, spellcheck: String(props.spellcheck), autocapitalize: 'sentences' })
}
function editability() { return [EditorView.editable.of(!props.readOnly), EditorState.readOnly.of(Boolean(props.readOnly))] }
function finishComposition(token: number) {
  if (disposed || token !== composition || !view) return
  if (view.compositionStarted) compositionTimer = setTimeout(() => finishComposition(token), 16)
  else compositionTimer = setTimeout(() => { if (!disposed && token === composition && composing) { composing = false; emit('compositionEnd') } }, 16)
}
onMounted(() => {
  view = new EditorView({ parent: root.value, state: EditorState.create({ doc: props.initialContent, extensions: [
    history(), keymap.of([...defaultKeymap, ...historyKeymap]), props.lineNumbers ? lineNumbers() : [], markdown(), EditorView.lineWrapping,
    attributes.of(contentAttributes()), editable.of(editability()),
    EditorView.domEventObservers({ compositionstart: startComposition, compositionend: endComposition }),
    // Android CodeMirror uses EditContext, whose composition events do not
    // bubble through contentDOM. Observe the owned context without replacing
    // it or disabling the browser input API. Bind before consumer listeners.
    EditorView.updateListener.of(update => bindEditContext(update.view)),
    EditorView.theme({
      '&': { height: '100%', color: 'var(--color-text-primary)', backgroundColor: 'var(--color-background-primary)' },
      '.cm-scroller': { fontFamily: 'var(--font-editor-mono)', fontSize: 'var(--font-editor-size)', overflow: 'auto' },
      '.cm-gutters': { backgroundColor: 'var(--color-background-secondary)', color: 'var(--color-text-secondary)', border: 'none' },
      '.cm-content': { padding: '24px 8px', minHeight: '100%' },
      '.cm-cursor': { borderLeftColor: 'var(--color-accent-primary)' },
      '&.cm-focused .cm-selectionBackground, .cm-selectionBackground': { background: 'var(--color-markdown-selection) !important' },
    }),
    props.extensions,
  ] }) })
  bindEditContext(view)
  emit('ready', view)
})
watch(() => [props.label, props.language, props.spellcheck, props.readOnly], () => {
  view?.dispatch({ effects: [attributes.reconfigure(contentAttributes()), editable.reconfigure(editability())], annotations: Transaction.addToHistory.of(false) })
})
onBeforeUnmount(() => { disposed = true; composition++; bindEditContext(); if (compositionTimer) clearTimeout(compositionTimer); view?.destroy(); view = undefined })
</script>

<template><div ref="root" class="source-editor-surface" :class="{ 'touch-editor': touch }" /></template>

<style scoped>
.source-editor-surface { flex: 1; min-height: 0; overflow: hidden; }
.touch-editor :deep(.cm-scroller) { font-size: max(16px, var(--font-editor-size)); line-height: 1.7; }
.touch-editor :deep(.cm-content) { padding: 18px 16px max(90px, env(safe-area-inset-bottom)); }
</style>
