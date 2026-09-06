<script setup lang="ts">
import { defineAsyncComponent, ref } from 'vue'
import { useWorkspaceStore } from '@/stores/workspace'
import EditorHeader from '@/features/editor/EditorHeader.vue'
import EditorPane from '@/features/editor/EditorPane.vue'
import WorkspacePluginCommands from './WorkspacePluginCommands.vue'
import { EditPen } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'
import { t } from '@/i18n'

const WorkspaceChat = defineAsyncComponent(() => import('../chat/WorkspaceChat.vue'))
const chatOpened = ref(false), chatVisible = ref(false)
function openChat() { chatOpened.value = true; chatVisible.value = true }
const workspaceStore = useWorkspaceStore()
</script>

<template>
  <div class="workspace-view">
    <button class="workspace-chat-launcher button-secondary" aria-label="唤起 AI 聊天" title="AI 聊天" @click="openChat">AI</button>
    <WorkspaceChat v-if="chatOpened" :open="chatVisible" @close="chatVisible = false" />
    <template v-if="workspaceStore.activeFilePath">
      <EditorHeader />
      <WorkspacePluginCommands><EditorPane /></WorkspacePluginCommands>
    </template>
    <div v-else class="empty-workspace">
      <div class="empty-content">
        <AppIcon class="empty-icon" :icon="EditPen" :size="48" />
        <h2>{{ t('开始写作', 'Start writing') }}</h2>
        <p>{{ t('从左侧文件树选择笔记，或创建新的笔记', 'Select a note from the file tree or create a new one') }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.workspace-chat-launcher { position: absolute; right: 24px; bottom: 76px; z-index: 11; width: 42px; height: 42px; border-radius: var(--radius-full); background: var(--color-editor-scroll-background); color: var(--color-editor-scroll-text); box-shadow: var(--shadow-sm); }

.workspace-view {
  position: relative;
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
