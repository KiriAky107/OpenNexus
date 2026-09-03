<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import type { FileNode } from '@/contracts'
import * as workspaceService from '@/services/workspaceService'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import FileTreeNode from './FileTreeNode.vue'
import { DocumentAdd, FolderAdd } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'

const workspaceStore = useWorkspaceStore()
const editorStore = useEditorStore()
const router = useRouter()
const newItemType = ref<'file' | 'folder' | null>(null)
const newItemName = ref('')
const parentPath = ref('/')
const selectedTreePath = ref(workspaceStore.activeFilePath ?? '/')
const selectedFolderPath = ref(
  workspaceStore.activeFilePath ? containingFolder(workspaceStore.activeFilePath) : '/',
)
const contextTarget = ref<FileNode | null>(null)
const contextMenuPosition = ref({ x: 0, y: 0 })

watch(() => workspaceStore.activeFilePath, (path) => {
  if (!path) return
  selectedTreePath.value = path
  selectedFolderPath.value = containingFolder(path)
})

function beginCreate(type: 'file' | 'folder', parent = '/') {
  newItemType.value = type
  newItemName.value = ''
  parentPath.value = parent
}

async function createItem() {
  const rawName = newItemName.value.trim()
  if (!rawName || !newItemType.value) return
  if (newItemType.value === 'file') {
    const name = rawName.endsWith('.md') ? rawName : `${rawName}.md`
    const file = await workspaceService.createFile(parentPath.value, name, `# ${rawName}\n\n`)
    workspaceStore.addFileToTree(parentPath.value, file)
    selectedTreePath.value = file.path
    selectedFolderPath.value = parentPath.value
    await editorStore.loadFile(file.path)
    workspaceStore.openFile(file.path)
    await router.push('/workspace')
  } else {
    const folder = await workspaceService.createFolder(parentPath.value, rawName)
    workspaceStore.addFileToTree(parentPath.value, folder)
    selectedTreePath.value = folder.path
    selectedFolderPath.value = folder.path
  }
  newItemType.value = null
  newItemName.value = ''
}

async function openNode(node: FileNode) {
  selectedTreePath.value = node.path
  if (node.type === 'folder') {
    selectedFolderPath.value = node.path
    return workspaceStore.toggleFolder(node.path)
  }
  selectedFolderPath.value = containingFolder(node.path)
  // 先同步活动文件，让真实点击立即生效；内容加载失败时再恢复原状态。
  const previousPath = workspaceStore.activeFilePath
  const wasOpen = workspaceStore.openFiles.includes(node.path)
  workspaceStore.openFile(node.path)
  try {
    await editorStore.loadFile(node.path)
    await router.push('/workspace')
  } catch (error) {
    if (!wasOpen) workspaceStore.closeFile(node.path)
    workspaceStore.setActiveFile(previousPath)
    console.error(`打开文件失败：${node.path}`, error)
  }
}

function openContextMenu(event: MouseEvent, node: FileNode) {
  event.preventDefault()
  event.stopPropagation()
  selectedTreePath.value = node.path
  selectedFolderPath.value = node.type === 'folder' ? node.path : containingFolder(node.path)
  contextTarget.value = node
  contextMenuPosition.value = { x: event.clientX, y: event.clientY }
}

function closeContextMenu() { contextTarget.value = null }

async function renameTarget() {
  const node = contextTarget.value
  if (!node) return
  const newName = window.prompt('新名称', node.name)?.trim()
  if (newName && newName !== node.name) {
    const normalizedName = node.type === 'file' && !newName.toLowerCase().endsWith('.md') ? `${newName}.md` : newName
    const oldPath = node.path
    const separator = oldPath.lastIndexOf('/')
    const newPath = `${oldPath.slice(0, separator + 1)}${normalizedName}`
    await workspaceService.renameFile(oldPath, normalizedName)
    workspaceStore.renamePath(oldPath, newPath, normalizedName)
    editorStore.renameFilePath(oldPath, newPath)
    if (selectedTreePath.value === oldPath || selectedTreePath.value.startsWith(`${oldPath}/`)) {
      selectedTreePath.value = `${newPath}${selectedTreePath.value.slice(oldPath.length)}`
    }
    if (selectedFolderPath.value === oldPath || selectedFolderPath.value.startsWith(`${oldPath}/`)) {
      selectedFolderPath.value = `${newPath}${selectedFolderPath.value.slice(oldPath.length)}`
    }
  }
  closeContextMenu()
}

async function deleteTarget() {
  const node = contextTarget.value
  if (!node) return
  if (!window.confirm(`确定要删除“${node.name}”吗？`)) return closeContextMenu()
  await workspaceService.deleteFile(node.path)
  const activeWasRemoved = workspaceStore.closePath(node.path)
  workspaceStore.removeFromTree(node.path)
  if (selectedTreePath.value === node.path || selectedTreePath.value.startsWith(`${node.path}/`)) {
    selectedTreePath.value = containingFolder(node.path)
    selectedFolderPath.value = selectedTreePath.value
  }
  if (activeWasRemoved) {
    editorStore.closeFile()
    if (workspaceStore.activeFilePath) await editorStore.loadFile(workspaceStore.activeFilePath)
  }
  closeContextMenu()
}

function containingFolder(path: string): string {
  const separator = path.lastIndexOf('/')
  return separator > 0 ? path.slice(0, separator) : '/'
}
</script>

<template>
  <section class="file-tree-panel" @click="closeContextMenu">
    <div class="toolbar">
      <button type="button" title="新建笔记" aria-label="新建笔记" @click.stop="beginCreate('file', selectedFolderPath)"><AppIcon :icon="DocumentAdd" /></button>
      <button type="button" title="新建文件夹" aria-label="新建文件夹" @click.stop="beginCreate('folder', selectedFolderPath)"><AppIcon :icon="FolderAdd" /></button>
    </div>
    <form v-if="newItemType" class="new-item" @submit.prevent="createItem">
      <input v-model="newItemName" :placeholder="newItemType === 'file' ? '笔记名称' : '文件夹名称'" autofocus />
      <button type="submit">创建</button>
      <button type="button" @click="newItemType = null">取消</button>
    </form>
    <div class="tree">
      <FileTreeNode v-for="node in workspaceStore.fileTree" :key="node.id" :node="node"
        :active-path="selectedTreePath" @open="openNode" @context-menu="openContextMenu" />
    </div>
    <Teleport to="body">
      <div v-if="contextTarget" class="context-menu"
        :style="{ left: `${contextMenuPosition.x}px`, top: `${contextMenuPosition.y}px` }" @click.stop>
        <button @click="renameTarget">重命名</button>
        <button class="danger" @click="deleteTarget">删除</button>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.file-tree-panel { height: 100%; }
.toolbar { display: flex; gap: var(--space-xs); padding: var(--space-sm); border-bottom: 1px solid var(--color-border-subtle); }
button { border: 0; border-radius: var(--radius-sm); padding: var(--space-xs) var(--space-sm); background: transparent; color: inherit; cursor: pointer; }
button:hover { background: var(--color-background-secondary); }
.new-item { display: flex; gap: var(--space-xs); padding: var(--space-sm); }
.new-item input { min-width: 0; flex: 1; }
.tree { padding: var(--space-xs); }
.context-menu { position: fixed; z-index: 1000; display: grid; min-width: 130px; padding: var(--space-xs); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-background-primary); box-shadow: var(--shadow-md); }
.context-menu button { text-align: left; }
.context-menu .danger { color: var(--color-error); }
</style>
