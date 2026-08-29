<script setup lang="ts">
import { onMounted } from 'vue'
import { useWorkspaceStore } from '@/stores/workspace'
import { useEditorStore } from '@/stores/editor'
import EditorHeader from '@/features/editor/EditorHeader.vue'
import EditorPane from '@/features/editor/EditorPane.vue'

const workspaceStore = useWorkspaceStore()
const editorStore = useEditorStore()

onMounted(() => {
  if (!workspaceStore.fileTree.length && workspaceStore.hasVault) {
    // Already loaded
  }
  if (!workspaceStore.activeFilePath && workspaceStore.fileTree.length === 0) {
    void editorStore.loadFile('/欢迎使用知笔知己.md').then(() => {
      workspaceStore.openFile('/欢迎使用知笔知己.md')
    })
  }
})
</script>

<template>
  <div class="workspace-view">
    <template v-if="workspaceStore.activeFilePath">
      <EditorHeader />
      <EditorPane />
    </template>
    <div v-else class="empty-workspace">
      <div class="empty-content">
        <div class="empty-icon">📝</div>
        <h2>开始写作</h2>
        <p>从左侧文件树选择笔记，或创建新的笔记</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.workspace-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.empty-workspace {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-tertiary);
}

.empty-content {
  text-align: center;

  h2 {
    font-size: 18px;
    color: var(--color-text-secondary);
    margin: 12px 0 8px;
  }

  p {
    font-size: 14px;
  }
}

.empty-icon {
  font-size: 48px;
  opacity: 0.5;
}
</style>
