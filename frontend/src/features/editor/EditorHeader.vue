<script setup lang="ts">
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import EditorToolbar from './EditorToolbar.vue'
import ExportDialog from './ExportDialog.vue'
import NoteHistory from './NoteHistory.vue'
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import ActionDialog from '@/components/common/ActionDialog.vue'
import { useActionDialog } from '@/composables/useActionDialog'
import { isDesktop } from '@/services/platform/desktop'
import { t } from '@/i18n'
import ControlIcon from '@/components/common/ControlIcon.vue'
import { useLayoutPreferencesStore } from '@/stores/layoutPreferences'
import { workspaceDocumentType } from '@/services/workspaceDocuments'
import { captureWorkspaceSelection, openWorkspaceExtensions } from '@/services/workspaceCommandService'

const editorStore = useEditorStore()
const layout = useLayoutPreferencesStore()
const workspaceStore = useWorkspaceStore()
const { actionDialog, resolveAction, askConfirm } = useActionDialog()
const reloadError = ref('')
let reloadVersion = 0
let disposed = false
onBeforeUnmount(() => { disposed = true; reloadVersion++ })
const exportOpen = ref(false)
const historyOpen = ref(false)
const desktop = isDesktop()
const actionsHost = ref<HTMLElement | null>(null)
onMounted(() => { actionsHost.value = document.getElementById('editor-actions-host') })
const documentType = computed(() => workspaceDocumentType(editorStore.currentFilePath ?? 'untitled.md'))
const isMarkdown = computed(() => documentType.value === 'markdown')
const needsRecovery = computed(() => ['conflict', 'external_changed'].includes(editorStore.saveStatus))
const missingFile = computed(() => needsRecovery.value && editorStore.currentFilePath === workspaceStore.activeFilePath && !workspaceStore.activeFile && !workspaceStore.treeRefreshError)
function downloadCopy() {
  const name = editorStore.currentFilePath?.split('/').pop() ?? 'document.md'
  const suffix = /\.([^.]+)$/.exec(name)
  const extension = suffix?.[1] ?? 'txt'
  const stem = suffix ? name.slice(0, -suffix[0].length) : name
  const url = URL.createObjectURL(new Blob([editorStore.content], { type: 'text/plain;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `${stem}-recovered.${extension}`
  document.body.append(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
async function discard() {
  const path = editorStore.currentFilePath, snapshot = editorStore.content
  if (!path || !(await askConfirm(t('关闭将丢弃当前编辑内容。需要保留时，请先下载副本。确认关闭？', 'Closing discards the current editor content. Download a copy first if needed. Close?')))) return
  if (!(await editorStore.discardExternalChanges(path, snapshot))) return
  workspaceStore.closeFile(path)
  workspaceStore.setActiveFile(null)
  reloadError.value = ''
}
async function reload() {
  const snapshot = editorStore.content
  const ownsDocument = editorStore.captureDocument()
  if (!(await askConfirm(t('重新加载会丢弃当前未保存内容。请先复制需要保留的文字。继续吗？', 'Reload discards unsaved edits. Copy any text you need to keep first. Continue?')))) return
  if (!ownsDocument() || snapshot !== editorStore.content) return
  const version = ++reloadVersion
  reloadError.value = ''
  try { await editorStore.reloadExternalFile() }
  catch (error) {
    if (disposed || version !== reloadVersion || !ownsDocument() || snapshot !== editorStore.content) return
    reloadError.value = error instanceof Error ? error.message : t('重新加载失败', 'Reload failed')
  }
}

const statusText = computed<Record<string, string>>(() => ({
  idle: t('空闲', 'Idle'), dirty: t('未保存', 'Unsaved'), saving: t('保存中…', 'Saving…'), saved: t('已保存', 'Saved'), save_failed: t('保存失败', 'Save failed'),
  external_changed: t('外部文件已变化', 'File changed externally'), conflict: t('存在编辑冲突', 'Edit conflict'),
}))
</script>

<template>
  <Teleport :to="actionsHost ?? 'body'" :disabled="!actionsHost || needsRecovery">
  <EditorToolbar :docked="Boolean(actionsHost && !needsRecovery)" :status-text="statusText[editorStore.saveStatus] ?? ''" :status-kind="editorStore.saveStatus" :can-save="!['saving','conflict','external_changed'].includes(editorStore.saveStatus)" :modes="isMarkdown ? [{id:'wysiwyg',label:t('写作','Writing')},{id:'source',label:t('源码','Source')}] : []" :mode="editorStore.mode" @mode="value=>editorStore.setMode(value as 'wysiwyg'|'source')" @save="editorStore.save">
    <template #dialogs>
    <ExportDialog v-if="exportOpen && isMarkdown" @close="exportOpen = false" />
    <NoteHistory v-if="historyOpen && isMarkdown && editorStore.currentNoteId" :note-id="editorStore.currentNoteId" @close="historyOpen = false" />
    <ActionDialog v-if="actionDialog" v-bind="actionDialog" @resolve="resolveAction" />
    </template>
    <template #actions>
      <button v-if="!actionsHost" class="button-secondary" @pointerdown="captureWorkspaceSelection" @click="openWorkspaceExtensions">{{ t('扩展命令', 'Extension commands') }}</button>
      <button v-if="isMarkdown && editorStore.currentNoteId" class="button-secondary" @click="historyOpen = true">{{ t('修改历史', 'Change history') }}</button>
      <button v-if="isMarkdown && editorStore.mode === 'wysiwyg'" type="button" class="toolbar-toggle" :aria-pressed="layout.editorToolbarVisible" :title="layout.editorToolbarVisible ? t('隐藏编辑器工具栏', 'Hide editor toolbar') : t('显示编辑器工具栏', 'Show editor toolbar')" :aria-label="layout.editorToolbarVisible ? t('隐藏编辑器工具栏', 'Hide editor toolbar') : t('显示编辑器工具栏', 'Show editor toolbar')" @click="layout.editorToolbarVisible = !layout.editorToolbarVisible"><ControlIcon name="toolbar" /></button>
      <button v-if="!desktop && isMarkdown" class="button-secondary" @click="exportOpen = true">{{ t('导出', 'Export') }}</button>
    </template>
    <template #status>
      <span v-if="editorStore.loadingFilePath" class="save-status" role="status">{{ t('正在打开文件…', 'Opening file…') }}</span>
      <span v-if="editorStore.externalReadError" class="save-status conflict" role="alert">{{ t('外部更新暂时读取失败，将自动重试', 'Could not read the external update; retrying automatically') }}</span>
      <span v-if="editorStore.blockNavigationNotice" class="save-status" role="status">{{ editorStore.blockNavigationNotice }}</span>
      <button v-if="needsRecovery && !missingFile" class="button-secondary" @click="reload">{{ t('重新加载外部版本', 'Reload external version') }}</button>
      <span v-if="missingFile" class="save-status conflict">{{ t('原文件已删除或移动', 'Original file deleted or moved') }}</span>
      <button v-if="needsRecovery" class="button-secondary" @click="downloadCopy">{{ isMarkdown ? t('下载 Markdown 副本', 'Download Markdown copy') : documentType === 'canvas' ? t('下载 Canvas 副本', 'Download Canvas copy') : t('下载文件副本', 'Download file copy') }}</button>
      <button v-if="needsRecovery" class="button-secondary" @click="discard">{{ isMarkdown ? t('关闭当前笔记', 'Close current note') : t('关闭当前文件', 'Close current file') }}</button>
      <span v-if="reloadError" class="save-status conflict" role="alert">{{ reloadError }}</span>
    </template>
  </EditorToolbar>
  </Teleport>
</template>
