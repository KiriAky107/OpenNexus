<script setup lang="ts">
import { onMounted } from 'vue'
import { useWorkspaceStore } from '@/stores/workspace'
import { useEditorStore } from '@/stores/editor'
import EditorHeader from '@/features/editor/EditorHeader.vue'
import EditorPane from '@/features/editor/EditorPane.vue'
import { EditPen } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'

const workspaceStore = useWorkspaceStore()
const editorStore = useEditorStore()

onMounted(() => {
  if (!workspaceStore.fileTree.length && workspaceStore.hasVault) {
    // Already loaded
  }
  if (!workspaceStore.activeFilePath && workspaceStore.fileTree.length === 0) {
    void editorStore.loadFile('/欢迎使用知笔知己.md').then(() => {
      // 默认文件加载期间用户可能已经点击了其他文件，不能覆盖用户的选择。
      if (!workspaceStore.activeFilePath && editorStore.currentFilePath === '/欢迎使用知笔知己.md') {
        workspaceStore.openFile('/欢迎使用知笔知己.md')
      }
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
        <AppIcon class="empty-icon" :icon="EditPen" :size="48" />
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
  padding: var(--space-3xl);
  border: 1px dashed var(--color-border-default);
  border-radius: var(--radius-xl);
  background: var(--color-background-secondary);
  animation: workspace-empty-in var(--motion-normal) both;

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

@keyframes workspace-empty-in { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
</style>
