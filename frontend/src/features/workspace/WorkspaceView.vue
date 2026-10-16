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
import WorkspaceLanding from './WorkspaceLanding.vue'
import { t } from '@/i18n'
import FolderContents from './FolderContents.vue'
import { useExperimentPane } from '@/composables/useExperimentPane'

const WorkspaceChat = defineAsyncComponent(() => import('../chat/WorkspaceChat.vue'))
const ExperimentPanel = defineAsyncComponent(() => import('../experiments/ExperimentPanel.vue'))
const experimentPane = useExperimentPane()
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
  try { await editorStore.loadFile(path) }
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
    <EditorHeader v-if="!workspaceStore.activeFolderPath && workspaceStore.activeFilePath" />
    <div class="workspace-content">
    <FolderContents v-if="workspaceStore.activeFolderPath" :path="workspaceStore.activeFolderPath" />
    <template v-else-if="workspaceStore.activeFilePath">
      <WorkspacePluginCommands><EditorPane /></WorkspacePluginCommands>
    </template>
    <WorkspaceLanding v-else :notes="notes" :creating="creating" :error="pageError" @new-note="newNote" @open-note="openNote" @import-recording="router.push({ name: 'media' })" @open-chat="openChat" />
    <ExperimentPanel v-if="experimentPane.visible.value && workspaceStore.hasVault" />
    </div>
  </div>
</template>

<style scoped>
.workspace-chat-launcher { position: absolute; right: 72px; bottom: 24px; z-index: 11; width: 42px; height: 42px; border-radius: var(--radius-full); background: var(--color-editor-scroll-background); color: var(--color-editor-scroll-text); box-shadow: var(--shadow-sm); }
.workspace-view:has(.canvas-editor) .workspace-chat-launcher { right: 16px; bottom: 64px; }

.workspace-view {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}
.workspace-content { position:relative; display:flex; flex:1; min-height:0; min-width:0; }

</style>
