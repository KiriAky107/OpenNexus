<script setup lang="ts">
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { t } from '@/i18n'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Compartment, EditorState, Text } from '@codemirror/state'
import { drawSelection, EditorView, highlightActiveLine, keymap, lineNumbers } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, indentWithTab, redo, undo } from '@codemirror/commands'
import { bracketMatching, foldGutter, foldKeymap, HighlightStyle, indentOnInput, indentUnit, syntaxHighlighting } from '@codemirror/language'
import { python } from '@codemirror/lang-python'
import { json } from '@codemirror/lang-json'
import { tags } from '@lezer/highlight'
import { parseCsvPreview } from './csvPreview'
import { useExperimentPane } from '@/composables/useExperimentPane'
import { registerEditorCommands, updateNativeEditorMenu } from '@/services/editorCommandService'
import { MAX_EXPERIMENT_EDITOR_BYTES } from '@/services/workspaceDocuments'

const editor = useEditorStore()
const settings = useSettingsStore()
const experimentPane = useExperimentPane()
const tablePreview = ref(false)
const root = ref<HTMLElement | null>(null)
const notice = ref('')
const isCurrentDocument = editor.captureDocument()
const lineEnding = editor.content.includes('\r\n') ? '\r\n' : '\n'
const access = new Compartment(), labels = new Compartment()
const byteSize = computed(() => new TextEncoder().encode(editor.content).length)
const locked = computed(() => ['conflict', 'external_changed'].includes(editor.saveStatus) || byteSize.value > MAX_EXPERIMENT_EDITOR_BYTES)
const language = editor.currentFilePath?.toLowerCase().endsWith('.py') ? python()
  : editor.currentFilePath?.toLowerCase().endsWith('.json') ? json() : []
let view: EditorView | undefined, dispose: (() => void) | undefined
const composing = ref(false)
const isCsv = computed(() => editor.currentFilePath?.toLowerCase().endsWith('.csv') ?? false)
const csvPreview = computed(() => isCsv.value ? parseCsvPreview(editor.content) : null)
function source(text: Text) { return text.sliceString(0, text.length, lineEnding) }
function available() { return isCurrentDocument() && !!view && !!editor.currentFilePath && !locked.value && !tablePreview.value }
function saveAfterInput() {
  if (!view || !isCurrentDocument()) return
  if (composing.value) editor.cancelPendingAutoSave()
  else if (!locked.value && ['dirty', 'save_failed'].includes(editor.saveStatus)) editor.scheduleAutoSave(settings.autoSaveInterval)
}
function attributes() {
  return EditorView.contentAttributes.of({ spellcheck: 'false', lang: settings.language,
    'aria-label': t('实验文件源码', 'Experiment source') })
}
const highlighting = HighlightStyle.define([
  { tag: tags.keyword, color: 'var(--color-accent-primary)' },
  { tag: [tags.string, tags.special(tags.string)], color: 'var(--color-success)' },
  { tag: [tags.number, tags.bool, tags.null, tags.function(tags.variableName)], color: 'var(--color-info)' },
  { tag: tags.comment, color: 'var(--color-text-secondary)', fontStyle: 'italic' },
  { tag: tags.invalid, color: 'var(--color-error)', textDecoration: 'underline' },
])
onMounted(() => {
  // Large files remain intact in the store; never render or save a truncated copy.
  if (byteSize.value > MAX_EXPERIMENT_EDITOR_BYTES) {
    dispose = registerEditorCommands({ available: () => false, handlers: {} })
    return
  }
  view = new EditorView({ parent: root.value!, state: EditorState.create({ doc: editor.content, extensions: [
    history(), keymap.of([...defaultKeymap, ...historyKeymap, ...foldKeymap, indentWithTab]),
    lineNumbers(), drawSelection(), highlightActiveLine(), foldGutter(), bracketMatching(), indentOnInput(), indentUnit.of('    '),
    language, syntaxHighlighting(highlighting), labels.of(attributes()),
    access.of([EditorState.readOnly.of(locked.value), EditorView.editable.of(!locked.value)]),
    EditorState.transactionFilter.of(transaction => {
      if (!transaction.docChanged) return transaction
      if (!available()) return []
      if (new TextEncoder().encode(source(transaction.newDoc)).length > MAX_EXPERIMENT_EDITOR_BYTES) {
        notice.value = t('编辑后会超过 2 MiB 上限，本次输入未应用，原内容已保留。', 'This edit would exceed the 2 MiB limit. It was not applied; your source is preserved.')
        return []
      }
      notice.value = ''
      return transaction
    }),
    EditorView.domEventObservers({
      compositionstart() { composing.value = true; if (isCurrentDocument()) editor.cancelPendingAutoSave() },
      compositionend() { composing.value = false; queueMicrotask(saveAfterInput) },
    }),
    EditorView.updateListener.of(update => {
      if (!isCurrentDocument()) return
      if (update.docChanged) {
        const content = source(update.state.doc)
        if (content !== editor.content) editor.updateContent(content)
        saveAfterInput()
      }
      if (update.docChanged || update.selectionSet) {
        const cursor = update.state.selection.main.head, line = update.state.doc.lineAt(cursor)
        editor.cursorPosition = { line: line.number, column: cursor - line.from + 1 }
      }
    }),
    EditorView.theme({
      '&': { height: '100%', color: 'var(--color-text-primary)', backgroundColor: 'var(--color-background-primary)' },
      '&.cm-focused': { outline: 'none' },
      '.cm-scroller': { fontFamily: 'var(--font-editor-mono)', fontSize: 'var(--font-editor-size)', lineHeight: 'var(--font-editor-line-height)', overflow: 'auto' },
      '.cm-content': { padding: '20px 8px', minHeight: '100%', caretColor: 'var(--color-text-primary)' },
      '.cm-cursor': { borderLeftColor: 'var(--color-text-primary)' },
      '.cm-gutters': { backgroundColor: 'var(--color-background-secondary)', color: 'var(--color-text-secondary)', border: 'none' },
      '.cm-activeLine': { backgroundColor: 'var(--color-accent-soft)' },
      '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': { backgroundColor: 'var(--color-markdown-selection)' },
      '.cm-foldPlaceholder': { backgroundColor: 'var(--color-background-secondary)', color: 'var(--color-text-secondary)', borderColor: 'var(--color-border-default)' },
    }),
  ] }) })
  dispose = registerEditorCommands({ available, handlers: {
    'editor.undo': () => undo(view!) ? { ok: true } : { ok: false, reason: 'unavailable' },
    'editor.redo': () => redo(view!) ? { ok: true } : { ok: false, reason: 'unavailable' },
  } })
})
watch(locked, value => {
  view?.dispatch({ effects: access.reconfigure([EditorState.readOnly.of(value), EditorView.editable.of(!value)]) })
  if (value && isCurrentDocument()) editor.cancelPendingAutoSave()
  updateNativeEditorMenu()
})
watch(() => settings.language, () => view?.dispatch({ effects: labels.reconfigure(attributes()) }))
watch(tablePreview, async value => {
  updateNativeEditorMenu()
  if (!value) { await nextTick(); view?.requestMeasure(); if (isCurrentDocument()) view?.focus() }
})
onBeforeUnmount(() => { dispose?.(); view?.destroy(); view = undefined })
function togglePreview() {
  // Keep the same editor state and undo history when returning from the table.
  if (!composing.value) tablePreview.value = !tablePreview.value
}
</script>

<template>
  <section class="experiment-editor" :aria-label="editor.currentFilePath ?? 'Experiment file'">
    <header class="experiment-editor__header">
      <span>{{ editor.currentFilePath?.split('/').at(-1) }}</span>
      <div class="experiment-editor__actions">
        <button type="button" :aria-expanded="experimentPane.visible.value" aria-controls="experiment-panel" @click="experimentPane.open">{{ t('运行与成果', 'Runs and outputs') }}</button>
        <button v-if="isCsv" type="button" :aria-pressed="tablePreview" :disabled="composing" @click="togglePreview">{{ tablePreview ? t('编辑文本', 'Edit text') : t('表格预览', 'Table preview') }}</button>
        <span>{{ editor.saveStatus === 'saving' ? t('保存中…', 'Saving…') : editor.saveStatus === 'saved' ? t('已保存', 'Saved') : editor.saveStatus === 'dirty' ? t('未保存', 'Unsaved') : '' }}</span>
      </div>
    </header>
    <p class="experiment-editor__format">UTF-8 · {{ lineEnding === '\r\n' ? 'CRLF' : 'LF' }} · {{ (byteSize / 1024).toFixed(1) }} KiB / 2 MiB</p>
    <p v-if="byteSize > MAX_EXPERIMENT_EDITOR_BYTES" class="experiment-editor__notice" role="alert">{{ t('文件超过 2 MiB 编辑上限，内容完整保留。请使用外部编辑器缩小文件后重新打开。', 'The file exceeds the 2 MiB editing limit. Its full content is preserved. Reduce its size in an external editor and reopen it.') }}</p>
    <p v-else-if="notice" class="experiment-editor__notice" role="alert">{{ notice }}</p>
    <div v-if="tablePreview && csvPreview" class="experiment-editor__table-wrap" role="region" :aria-label="t('CSV 表格预览', 'CSV table preview')" tabindex="0">
      <p v-if="csvPreview.invalid" class="experiment-editor__notice" role="alert">{{ t('CSV 引号格式不正确，无法显示表格预览。', 'CSV quoting is malformed; the table preview is unavailable.') }}</p>
      <template v-else>
        <p v-if="csvPreview.truncated" class="experiment-editor__notice" role="status">{{ t('预览已限制为前 200 行、40 列、256K 字符和每格 4096 个字符；原文件内容未截断。', 'Preview is limited to 200 rows, 40 columns, 256K characters and 4096 characters per cell. The source file is unchanged.') }}</p>
        <table v-if="csvPreview.rows.length" class="experiment-editor__table"><tbody>
          <tr v-for="(row, rowIndex) in csvPreview.rows" :key="rowIndex">
            <td v-for="(cell, columnIndex) in row" :key="columnIndex">{{ cell }}</td>
          </tr>
        </tbody></table>
        <p v-else>{{ t('文件没有可预览的行。', 'No rows to preview.') }}</p>
      </template>
    </div>
    <div v-show="!tablePreview" ref="root" class="experiment-editor__source" />
  </section>
</template>

<style scoped>
.experiment-editor { display:flex; flex-direction:column; flex:1; min-height:0; min-width:0; background:var(--color-background-primary); color:var(--color-text-primary); }
.experiment-editor__header { display:flex; justify-content:space-between; gap:var(--space-md); padding:var(--space-sm) var(--space-xl); border-bottom:1px solid var(--color-border-subtle); color:var(--color-text-secondary); font-size:var(--font-size-sm); }
.experiment-editor__actions { display:flex; align-items:center; gap:var(--space-md); }
.experiment-editor__actions button { padding:4px 8px; border:1px solid var(--color-border-default); border-radius:var(--radius-sm); background:var(--color-background-secondary); color:var(--color-text-primary); }
.experiment-editor__format { margin:0; padding:4px var(--space-xl); color:var(--color-text-secondary); font-size:var(--font-size-sm); border-bottom:1px solid var(--color-border-subtle); }
.experiment-editor__table-wrap { flex:1; min-height:0; overflow:auto; padding:var(--space-md); }
.experiment-editor__notice { position:sticky; top:0; z-index:1; padding:var(--space-sm); background:var(--color-background-secondary); color:var(--color-text-secondary); }
.experiment-editor__table { border-collapse:collapse; font-size:var(--font-size-sm); }
.experiment-editor__table td { max-width:420px; padding:6px 10px; border:1px solid var(--color-border-default); white-space:pre-wrap; overflow-wrap:anywhere; }
.experiment-editor__source { flex:1; min-height:0; min-width:0; overflow:hidden; }
</style>
