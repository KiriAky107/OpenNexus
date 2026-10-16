<script setup lang="ts">
import { EditPen } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'
import { t } from '@/i18n'
withDefaults(defineProps<{ notes: { path: string; name: string }[]; creating?: boolean; error?: string }>(), { creating: false, error: '' })
const emit = defineEmits<{ newNote: []; openNote: [path: string]; importRecording: []; openChat: [] }>()
</script>

<template>
<div class="empty-workspace">
      <div class="empty-content">
        <AppIcon class="empty-icon" :icon="EditPen" :size="48" />
        <h2>{{ t('开始写作', 'Start writing') }}</h2>
        <p>{{ t('从左侧文件树选择笔记，或创建新的笔记', 'Select a note from the file tree or create a new one') }}</p>
        <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
        <div class="start-actions"><slot name="actions"><button class="button-primary" :disabled="creating" @click="emit('newNote')">{{ t('新建笔记', 'New note') }}</button><button class="button-secondary" @click="emit('importRecording')">{{ t('导入课程录音', 'Import recording') }}</button><button class="button-secondary" @click="emit('openChat')">{{ t('与笔记对话', 'Chat with notes') }}</button></slot></div>
        <div v-if="notes.length" class="note-shortcuts"><h3>{{ t('知识库中的笔记', 'Notes in this vault') }}</h3><button v-for="note in notes" :key="note.path" :title="note.path" @click="emit('openNote', note.path)"><span>{{ note.name.replace(/\.md$/i, '') }}</span><span aria-hidden="true">↗</span></button></div>
      </div>
    </div>
</template>

<style scoped>
.empty-workspace {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-tertiary);
  overflow: auto;
  padding: var(--space-xl);
}

.empty-content {
  width: min(620px, 100%);
  margin-block: auto;
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
.start-actions { display: flex; flex-wrap: wrap; justify-content: center; gap: 10px; margin-top: 24px; }
.note-shortcuts { text-align: left; margin-top: 28px; }
.note-shortcuts h3 { font-size: var(--font-size-sm); margin-bottom: 10px; color: var(--color-text-secondary); }
.note-shortcuts button { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; text-align: left; padding: 10px 0; color: var(--color-text-primary); border-top: 1px solid var(--color-border-subtle); }
.note-shortcuts button span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.note-shortcuts button:hover { color: var(--color-accent-primary); }

.empty-icon {
  font-size: 48px;
  opacity: 0.5;
}

@keyframes workspace-empty-in { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }

.start-actions :deep(button) { min-height: 34px; }
</style>
