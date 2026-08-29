<script setup lang="ts">
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'

const editorStore = useEditorStore()
const workspaceStore = useWorkspaceStore()
</script>

<template>
  <header class="editor-header">
    <strong>{{ workspaceStore.activeFile?.name ?? '未命名笔记' }}</strong>
    <span>{{ editorStore.saveStatus }}</span>
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
</style>
