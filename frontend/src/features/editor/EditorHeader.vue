<script setup lang="ts">
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'

const editorStore = useEditorStore()
const workspaceStore = useWorkspaceStore()

const statusText: Record<string, string> = {
  idle: '空闲', dirty: '未保存', saving: '保存中…', saved: '已保存', save_failed: '保存失败',
  external_changed: '外部文件已变化', conflict: '存在编辑冲突',
}
</script>

<template>
  <header class="editor-header">
    <div class="file-identity"><strong>{{ workspaceStore.activeFile?.name ?? '未命名笔记' }}</strong><small>{{ workspaceStore.activeFilePath }}</small></div>
    <div class="editor-actions">
      <span class="save-status" :class="editorStore.saveStatus">{{ statusText[editorStore.saveStatus] }}</span>
      <div class="mode-switch" aria-label="编辑模式">
        <button type="button" :class="{ active: editorStore.mode === 'wysiwyg' }" @click="editorStore.setMode('wysiwyg')">写作</button>
        <button type="button" :class="{ active: editorStore.mode === 'source' }" @click="editorStore.setMode('source')">源码</button>
      </div>
      <button type="button" class="save-button" :disabled="editorStore.saveStatus === 'saving'" @click="editorStore.save">保存</button>
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
