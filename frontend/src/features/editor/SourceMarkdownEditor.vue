<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorState, Compartment } from '@codemirror/state'
import { EditorView, keymap, lineNumbers } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, isolateHistory, undo, redo } from '@codemirror/commands'
import { markdown } from '@codemirror/lang-markdown'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { registerEditorCommands } from '@/services/editorCommandService'
import { previewPropertyImport, type PropertyChoices, type PropertyConflict } from './importProperties'
import AppDialog from '@/components/common/AppDialog.vue'
import { storeWorkspaceImage, type WorkspaceAssetSource } from '@/services/workspaceService'

const props = defineProps<{ initialContent: string }>()
const editor = useEditorStore(), settings = useSettingsStore()
const root = ref<HTMLElement | null>(null), error = ref('')
const imageInput = ref<HTMLInputElement | null>(null)
const conflicts = ref<PropertyConflict[]>([]), choices = ref<PropertyChoices>({})
const proofing = new Compartment()
let view: EditorView | undefined, dispose: (() => void) | undefined
let pending: { content: string; path: string | null; from: number; to: number; state: EditorState } | undefined
function attributes() {
  return EditorView.contentAttributes.of({ spellcheck: String(settings.spellCheck), lang: settings.language,
    'aria-label': settings.language === 'en' ? 'Markdown source editor' : 'Markdown 源码编辑器' })
}
function available() { return !!view && !!editor.currentFilePath && !['conflict', 'external_changed'].includes(editor.saveStatus) }
function imageFiles(list: FileList | null): File[] {
  return [...(list ?? [])].filter(file => file.type.startsWith('image/'))
}
async function insertImages(files: File[], source: WorkspaceAssetSource, position?: number) {
  if (!view || !available() || !files.length) return
  const targetView = view, targetPath = editor.currentFilePath
  const at = position ?? targetView.state.selection.main.from
  const document = targetView.state.doc
  error.value = ''
  try {
    const assets = []
    for (const file of files) assets.push(await storeWorkspaceImage(file, source, targetPath!, editor.currentNoteId))
    if (view !== targetView || editor.currentFilePath !== targetPath || !available()) return
    const markdown = assets.map(asset => `![${asset.original_name.replace(/[\]\\]/g, '\\$&')}](${asset.reference})`).join('\n\n')
    const insertion = targetView.state.doc.eq(document) ? Math.min(at, targetView.state.doc.length) : targetView.state.selection.main.from
    targetView.dispatch({ changes: { from: insertion, insert: markdown } })
    targetView.focus()
  } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
}
function chooseImages() { imageInput.value?.click() }
function selectedImages(event: Event) {
  const input = event.target as HTMLInputElement
  void insertImages(imageFiles(input.files), 'upload')
  input.value = ''
}
function importProperties() {
  if (!available() || !view) return { ok: false as const, reason: 'unavailable' as const }
  error.value = ''; choices.value = {}
  const selection = view.state.selection.main
  pending = { content: view.state.doc.toString(), path: editor.currentFilePath, from: selection.from, to: selection.to, state: view.state }
  try {
    const preview = previewPropertyImport(pending.content, selection)
    if (preview.conflicts.length) conflicts.value = preview.conflicts
    else applyImport()
    return { ok: true as const }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
    pending = undefined
    return { ok: false as const, reason: 'failed' as const }
  }
}
function applyImport() {
  if (!pending || !view) return
  // 弹窗期间文档或路径改变就取消，不能把旧预览写入新笔记或新版本。
  if (editor.currentFilePath !== pending.path || !view.state.doc.eq(pending.state.doc) || editor.content !== pending.content || !available()) {
    pending = undefined; conflicts.value = []; error.value = '文档已经变化，请重新导入。'; return
  }
  try {
    const result = previewPropertyImport(pending.content, { from: pending.from, to: pending.to }, choices.value)
    if (result.content === null) return
    if (result.content !== pending.content) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: result.content }, annotations: isolateHistory.of('full') })
    }
    pending = undefined; conflicts.value = []; view.focus()
  } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
}
onMounted(() => {
  view = new EditorView({ parent: root.value!, state: EditorState.create({ doc: props.initialContent, extensions: [
    history(), keymap.of([...defaultKeymap, ...historyKeymap]), lineNumbers(), markdown(), proofing.of(attributes()),
    EditorView.lineWrapping,
    EditorView.domEventHandlers({
      paste(event) {
        const files = imageFiles(event.clipboardData?.files ?? null)
        if (!files.length) return false
        event.preventDefault(); void insertImages(files, 'paste'); return true
      },
      drop(event, currentView) {
        const files = imageFiles(event.dataTransfer?.files ?? null)
        if (!files.length) return false
        event.preventDefault()
        const position = currentView.posAtCoords({ x: event.clientX, y: event.clientY }) ?? currentView.state.selection.main.from
        void insertImages(files, 'drop', position); return true
      },
    }),
    EditorView.updateListener.of(update => {
      if (update.docChanged) { editor.updateContent(update.state.doc.toString()); editor.scheduleAutoSave(settings.autoSaveInterval) }
    }),
    EditorView.theme({ '&': { height: '100%', color: 'var(--color-text-primary)', backgroundColor: 'var(--color-background-primary)' },
      '.cm-scroller': { fontFamily: 'var(--font-editor-mono)', fontSize: 'var(--font-editor-size)', overflow: 'auto' },
      '.cm-gutters': { backgroundColor: 'var(--color-background-secondary)', color: 'var(--color-text-secondary)', border: 'none' },
      '.cm-content': { padding: '24px 8px', minHeight: '100%' } }),
  ] }) })
  dispose = registerEditorCommands({ available, handlers: {
    'editor.import-note-properties': importProperties,
    'editor.undo': () => undo(view!) ? { ok: true } : { ok: false, reason: 'unavailable' },
    'editor.redo': () => redo(view!) ? { ok: true } : { ok: false, reason: 'unavailable' },
  } })
})
watch(() => [settings.spellCheck, settings.language], () => view?.dispatch({ effects: proofing.reconfigure(attributes()) }))
watch(() => editor.headingRequest, request => {
  if (!view || !request || request.path !== editor.currentFilePath) return
  const offset = Math.min(view.state.doc.length, request.offset)
  view.dispatch({ selection: { anchor: offset }, effects: EditorView.scrollIntoView(offset, { y: 'start' }) }); view.focus()
})
onBeforeUnmount(() => { dispose?.(); view?.destroy(); pending = undefined })
</script>

<template>
  <div class="source-container">
    <div class="source-actions">
      <button class="btn" :disabled="!editor.currentFilePath || ['conflict', 'external_changed'].includes(editor.saveStatus)" @click="importProperties">导入为笔记属性…</button>
      <button class="btn" :disabled="!available()" @click="chooseImages">插入图片…</button>
      <input ref="imageInput" class="visually-hidden" type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple @change="selectedImages" />
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
    <div ref="root" class="source-code" />
    <AppDialog v-if="conflicts.length" label="属性冲突预览" @close="conflicts = []; pending = undefined">
      <h2>选择要保留的属性</h2>
      <div v-for="conflict in conflicts" :key="conflict.key">
        <strong>{{ conflict.key }}</strong><pre>已有：{{ conflict.current }}
导入：{{ conflict.incoming }}</pre>
        <label>保留哪一侧 <select v-model="choices[conflict.key]"><option disabled value="">请选择</option><option value="current">已有属性</option><option value="incoming">导入属性</option></select></label>
      </div>
      <button class="btn btn-primary" :disabled="conflicts.some(item => !choices[item.key])" @click="applyImport">作为一次编辑应用</button>
    </AppDialog>
  </div>
</template>

<style scoped>
.source-container { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.source-code { flex: 1; min-height: 0; overflow: hidden; }
.source-actions { display: flex; gap: var(--space-sm); padding: var(--space-sm); border-bottom: 1px solid var(--color-border-subtle); }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
