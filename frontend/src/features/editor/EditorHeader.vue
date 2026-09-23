<script setup lang="ts">
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import ExportDialog from './ExportDialog.vue'
import { computed, ref } from 'vue'
import ActionDialog from '@/components/common/ActionDialog.vue'
import { useActionDialog } from '@/composables/useActionDialog'
import { isDesktop } from '@/services/platform/desktop'
import { t } from '@/i18n'
import ControlIcon from '@/components/common/ControlIcon.vue'
import { useLayoutPreferencesStore } from '@/stores/layoutPreferences'

const editorStore = useEditorStore()
const layout = useLayoutPreferencesStore()
const workspaceStore = useWorkspaceStore()
const { actionDialog, resolveAction, askConfirm } = useActionDialog()
const reloadError = ref('')
const exportOpen = ref(false)
const desktop = isDesktop()
const needsRecovery = computed(() => ['conflict', 'external_changed'].includes(editorStore.saveStatus))
const missingFile = computed(() => needsRecovery.value && editorStore.currentFilePath === workspaceStore.activeFilePath && !workspaceStore.activeFile && !workspaceStore.treeRefreshError)
function downloadCopy() {
  const url = URL.createObjectURL(new Blob([editorStore.content], { type: 'text/markdown;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `${(editorStore.currentFilePath?.split('/').pop() ?? 'note.md').replace(/\.md$/i, '')}-recovered.md`
  document.body.append(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
async function discard() {
  const path = editorStore.currentFilePath, snapshot = editorStore.content
  if (!path || !(await askConfirm(t('关闭将丢弃当前编辑内容。需要保留时，请先下载 Markdown 副本。确认关闭？', 'Closing discards the current editor content. Download a Markdown copy first if needed. Close?')))) return
  if (!(await editorStore.discardExternalChanges(path, snapshot))) return
  workspaceStore.closeFile(path)
  workspaceStore.setActiveFile(null)
  reloadError.value = ''
}
async function reload() {
  const path = editorStore.currentFilePath, snapshot = editorStore.content
  if (!(await askConfirm(t('重新加载会丢弃当前未保存内容。请先复制需要保留的文字。继续吗？', 'Reload discards unsaved edits. Copy any text you need to keep first. Continue?')))) return
  if (path !== editorStore.currentFilePath || snapshot !== editorStore.content) return
  try { await editorStore.reloadExternalFile(); reloadError.value = '' } catch (error) { reloadError.value = error instanceof Error ? error.message : '重新加载失败' }
}

const statusText = computed<Record<string, string>>(() => ({
  idle: t('空闲', 'Idle'), dirty: t('未保存', 'Unsaved'), saving: t('保存中…', 'Saving…'), saved: t('已保存', 'Saved'), save_failed: t('保存失败', 'Save failed'),
  external_changed: t('外部文件已变化', 'File changed externally'), conflict: t('存在编辑冲突', 'Edit conflict'),
}))
</script>

<template>
  <header class="editor-header">
    <ExportDialog v-if="exportOpen" @close="exportOpen = false" />
    <ActionDialog v-if="actionDialog" v-bind="actionDialog" @resolve="resolveAction" />
    <div class="file-identity"><strong>{{ workspaceStore.activeFile?.name ?? t('未命名笔记', 'Untitled note') }}</strong><small>{{ workspaceStore.activeFilePath }}</small></div>
    <div class="editor-actions">
      <button v-if="editorStore.mode === 'wysiwyg'" type="button" class="toolbar-toggle" :aria-pressed="layout.editorToolbarVisible" :title="layout.editorToolbarVisible ? t('隐藏编辑器工具栏', 'Hide editor toolbar') : t('显示编辑器工具栏', 'Show editor toolbar')" :aria-label="layout.editorToolbarVisible ? t('隐藏编辑器工具栏', 'Hide editor toolbar') : t('显示编辑器工具栏', 'Show editor toolbar')" @click="layout.editorToolbarVisible = !layout.editorToolbarVisible"><ControlIcon name="toolbar" /></button>
      <button v-if="!desktop" class="button-secondary" @click="exportOpen = true">{{ t('导出', 'Export') }}</button>
      <span class="save-status" :class="editorStore.saveStatus">{{ statusText[editorStore.saveStatus] }}</span>
      <button v-if="needsRecovery && !missingFile" class="button-secondary" @click="reload">{{ t('重新加载外部版本', 'Reload external version') }}</button>
      <span v-if="missingFile" class="save-status conflict">{{ t('原文件已删除或移动', 'Original file deleted or moved') }}</span>
      <button v-if="needsRecovery" class="button-secondary" @click="downloadCopy">{{ t('下载 Markdown 副本', 'Download Markdown copy') }}</button>
      <button v-if="needsRecovery" class="button-secondary" @click="discard">{{ t('关闭当前笔记', 'Close current note') }}</button>
      <span v-if="reloadError" class="save-status conflict" role="alert">{{ reloadError }}</span>
      <div class="mode-switch" :aria-label="t('编辑模式', 'Editor mode')">
        <button type="button" :class="{ active: editorStore.mode === 'wysiwyg' }" @click="editorStore.setMode('wysiwyg')">{{ t('写作', 'Writing') }}</button>
        <button type="button" :class="{ active: editorStore.mode === 'source' }" @click="editorStore.setMode('source')">{{ t('源码', 'Source') }}</button>
      </div>
      <button type="button" class="save-button" :disabled="['saving','conflict','external_changed'].includes(editorStore.saveStatus)" @click="editorStore.save">{{ t('保存', 'Save') }}</button>
    </div>
  </header>
</template>

<style scoped>
.editor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 40px;
  padding: 0 var(--space-lg);
  border-bottom: 1px solid var(--color-border-subtle);
  color: var(--color-text-secondary);
}
.file-identity { display: grid; min-width: 0; }
.file-identity strong, .file-identity small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-identity small { color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
.editor-actions { flex-wrap: wrap; justify-content: flex-end; }
.toolbar-toggle { display: grid; place-items: center; width: 32px; height: 32px; flex-shrink: 0; border-radius: var(--radius-sm); }
.toolbar-toggle:hover, .toolbar-toggle[aria-pressed="true"] { color: var(--color-accent-primary); background: var(--color-accent-soft); }
.toolbar-toggle:focus-visible { outline: 2px solid var(--color-border-focus); }
.editor-actions, .mode-switch { display: flex; align-items: center; gap: var(--space-sm); }
.save-status { color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
.save-status.dirty, .save-status.external_changed { color: var(--color-warning); }
.save-status.save_failed, .save-status.conflict { color: var(--color-error); }
.save-status.saved { color: var(--color-success); }
.mode-switch { gap: 2px; padding: 2px; border-radius: var(--radius-md); background: var(--color-background-secondary); }
.mode-switch button, .save-button { padding: 5px 9px; border-radius: var(--radius-sm); }
.mode-switch button.active { background: var(--color-surface-primary); color: var(--color-accent-primary); box-shadow: var(--shadow-sm); }
.save-button { background: var(--color-accent-primary); color: var(--color-text-inverse); }
.save-button:disabled { opacity: .55; }
</style>
