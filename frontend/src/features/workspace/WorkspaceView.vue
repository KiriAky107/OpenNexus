<script setup lang="ts">
import { computed, defineAsyncComponent, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useEditorStore } from '@/stores/editor'
import { createFile } from '@/services/workspaceService'
import { useActionDialog } from '@/composables/useActionDialog'
import ActionDialog from '@/components/common/ActionDialog.vue'
import type { FileNode } from '@/contracts'
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
const editorStore = useEditorStore()
const router = useRouter()
const { actionDialog, resolveAction, askPrompt } = useActionDialog()
const pageError = ref('')
const creating = ref(false)
const notes = computed(() => {
  const collect = (nodes: FileNode[]): FileNode[] => nodes.flatMap(node => node.type === 'file' && /\.md$/i.test(node.name) ? [node] : collect(node.children || []))
  return collect(workspaceStore.fileTree).slice(0, 5)
})
async function openNote(path: string) {
  try { await editorStore.loadFile(path); workspaceStore.openFile(path) }
  catch (error) { pageError.value = error instanceof Error ? error.message : String(error) }
}
async function newNote() {
  const title = (await askPrompt(t('笔记名称', 'Note name'), t('新笔记', 'New note')))?.trim()
  if (!title || creating.value) return
  if (/[\\/]/.test(title) || ['.', '..'].includes(title)) { pageError.value = t('名称不能包含路径分隔符', 'Names cannot contain path separators'); return }
  creating.value = true; pageError.value = ''
  try {
    const file = await createFile('/', title.endsWith('.md') ? title : `${title}.md`, `# ${title.replace(/\.md$/i, '')}\n\n`)
    workspaceStore.addFileToTree('/', file)
    await openNote(file.path)
  } catch (error) { pageError.value = error instanceof Error ? error.message : String(error) }
  finally { creating.value = false }
}
</script>

<template>
  <div class="workspace-view">
    <ActionDialog v-if="actionDialog" v-bind="actionDialog" @resolve="resolveAction" />
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
        <p v-if="pageError" class="error-banner" role="alert">{{ pageError }}</p>
        <div class="start-actions"><button class="button-primary" :disabled="creating" @click="newNote">{{ t('新建笔记', 'New note') }}</button><button class="button-secondary" @click="router.push({ name: 'media' })">{{ t('导入课程录音', 'Import recording') }}</button><button class="button-secondary" @click="openChat">{{ t('与笔记对话', 'Chat with notes') }}</button></div>
        <div v-if="notes.length" class="note-shortcuts"><h3>{{ t('知识库中的笔记', 'Notes in this vault') }}</h3><button v-for="note in notes" :key="note.path" :title="note.path" @click="openNote(note.path)"><span>{{ note.name.replace(/\.md$/i, '') }}</span><span aria-hidden="true">↗</span></button></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.workspace-chat-launcher { position: absolute; right: 72px; bottom: 24px; z-index: 11; width: 42px; height: 42px; border-radius: var(--radius-full); background: var(--color-editor-scroll-background); color: var(--color-editor-scroll-text); box-shadow: var(--shadow-sm); }

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
</style>
