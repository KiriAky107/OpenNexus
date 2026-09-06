<script setup lang="ts">
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import { computed, ref } from 'vue'
import ActionDialog from '@/components/common/ActionDialog.vue'
import { useActionDialog } from '@/composables/useActionDialog'
import { t } from '@/i18n'

const editorStore = useEditorStore()
const workspaceStore = useWorkspaceStore()
const { actionDialog, resolveAction, askConfirm } = useActionDialog()
const reloadError = ref('')
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
    <ActionDialog v-if="actionDialog" v-bind="actionDialog" @resolve="resolveAction" />
    <div class="file-identity"><strong>{{ workspaceStore.activeFile?.name ?? t('未命名笔记', 'Untitled note') }}</strong><small>{{ workspaceStore.activeFilePath }}</small></div>
    <div class="editor-actions">
      <span class="save-status" :class="editorStore.saveStatus">{{ statusText[editorStore.saveStatus] }}</span>
      <button v-if="['conflict', 'external_changed'].includes(editorStore.saveStatus)" class="button-secondary" @click="reload">{{ t('重新加载外部版本', 'Reload external version') }}</button>
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
