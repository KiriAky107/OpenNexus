<script setup lang="ts">
import { ref } from 'vue'
import { useWorkspaceStore } from '@/stores/workspace'
import { useEditorStore } from '@/stores/editor'
import { useRouter } from 'vue-router'
import type { FileNode } from '@/contracts'
import * as workspaceService from '@/services/workspaceService'

const workspaceStore = useWorkspaceStore()
const editorStore = useEditorStore()
const router = useRouter()

const showNewMenu = ref(false)
const newFileName = ref('')
const newFolderName = ref('')
const newFileParentPath = ref('')
const showNewFileInput = ref(false)
const showNewFolderInput = ref(false)
const contextMenuPath = ref<string | null>(null)
const showContextMenu = ref(false)
const contextMenuPos = ref({ x: 0, y: 0 })
const renamingPath = ref<string | null>(null)
const renameValue = ref('')

function toggleFolder(node: FileNode) {
  workspaceStore.toggleFolder(node.path)
}

async function openFile(node: FileNode) {
  if (node.type === 'folder') {
    toggleFolder(node)
    return
  }
  workspaceStore.openFile(node.path)
  await editorStore.loadFile(node.path)
  router.push('/workspace')
}

function startNewFile(parentPath = '') {
  newFileParentPath.value = parentPath
  showNewFileInput.value = true
  showNewMenu.value = false
  newFileName.value = ''
}

function startNewFolder(parentPath = '') {
  newFileParentPath.value = parentPath
  showNewFolderInput.value = true
  showNewMenu.value = false
  newFolderName.value = ''
}

async function createFile() {
  if (!newFileName.value.trim()) return
  const name = newFileName.value.endsWith('.md') ? newFileName.value : `${newFileName.value}.md`
  const file = await workspaceService.createFile(newFileParentPath.value || '/', name, '# ' + newFileName.value + '\n\n')
  workspaceStore.addFileToTree(newFileParentPath.value || '/', file)
  workspaceStore.openFile(file.path)
  await editorStore.loadFile(file.path)
  showNewFileInput.value = false
  newFileName.value = ''
}

async function createFolder() {
  if (!newFolderName.value.trim()) return
  const folder = await workspaceService.createFolder(newFileParentPath.value || '/', newFolderName.value)
  workspaceStore.addFileToTree(newFileParentPath.value || '/', folder)
  showNewFolderInput.value = false
  newFolderName.value = ''
}

function onContextMenu(e: MouseEvent, node: FileNode) {
  e.preventDefault()
  contextMenuPath.value = node.path
  contextMenuPos.value = { x: e.clientX, y: e.clientY }
  showContextMenu.value = true
}

function closeContextMenu() {
  showContextMenu.value = false
  contextMenuPath.value = null
}

function startRename(node: FileNode) {
  renamingPath.value = node.path
  renameValue.value = node.name
  showContextMenu.value = false
}

async function finishRename(node: FileNode) {
  if (renameValue.value && renameValue.value !== node.name) {
    await workspaceService.renameFile(node.path, renameValue.value)
    node.name = renameValue.value
  }
  renamingPath.value = null
}

async function deleteNode(node: FileNode) {
  const confirmMsg = node.type === 'folder' ? `确定要删除文件夹 "${node.name}" 吗？` : `确定要删除笔记 "${node.name}" 吗？`
  if (confirm(confirmMsg)) {
    await workspaceService.deleteFile(node.path)
    workspaceStore.removeFromTree(node.path)
    if (node.type === 'file') {
      workspaceStore.closeFile(node.path)
    }
  }
  showContextMenu.value = false
}

function getFileIcon(name: string) {
  if (name.endsWith('.md')) return '📄'
  return '📄'
}
</script>

<template>
  <div class="file-tree-panel" @click="closeContextMenu">
    <div class="panel-toolbar">
      <div class="toolbar-left">
        <button class="tool-btn" @click="startNewFile" title="新建笔记">
          <span>➕</span>
        </button>
        <button class="tool-btn" @click="startNewFolder" title="新建文件夹">
          <span>📁</span>
        </button>
      </div>
      <button class="tool-btn" title="刷新">
        <span>🔄</span>
      </button>
    </div>

    <div class="new-input" v-if="showNewFileInput">
      <input
        v-model="newFileName"
        type="text"
        placeholder="笔记名称"
        @keyup.enter="createFile"
        @keyup.esc="showNewFileInput = false"
        autofocus
      />
    </div>
    <div class="new-input" v-if="showNewFolderInput">
      <input
        v-model="newFolderName"
        type="text"
        placeholder="文件夹名称"
        @keyup.enter="createFolder"
        @keyup.esc="showNewFolderInput = false"
        autofocus
      />
    </div>

    <div class="tree-container">
      <template v-for="node in workspaceStore.fileTree" :key="node.id">
        <div class="tree-node-wrapper">
          <TreeNode :node="node" :depth="0" @open="openFile" @toggle="toggleFolder" @context-menu="onContextMenu"
            :renaming-path="renamingPath" :rename-value="renameValue"
            @rename-start="startRename" @rename-finish="finishRename"
            @delete-node="deleteNode"
            @new-file="startNewFile" @new-folder="startNewFolder" />
        </div>
      </template>
    </div>

    <Teleport to="body">
      <div v-if="showContextMenu" class="context-menu"
        :style="{ left: contextMenuPos.x + 'px', top: contextMenuPos.y + 'px' }"
        @click.stop>
        <button @click="() => { const n = workspaceStore.activeFile; if (n) startRename(n) }">✏️ 重命名</button>
        <button @click="() => { const n = workspaceStore.activeFile; if (n) deleteNode(n) }" class="danger">🗑️ 删除</button>
      </div>
    </Teleport>
  </div>
</template>

<script lang="ts">
import { defineComponent, h } from 'vue'
import type { FileNode } from '@/contracts'

const TreeNode = defineComponent({
  name: 'TreeNode',
  props: {
    node: { type: Object as () => FileNode, required: true },
    depth: { type: Number, default: 0 },
    renamingPath: { type: String, default: null },
    renameValue: { type: String, default: '' },
  },
  emits: ['open', 'toggle', 'context-menu', 'rename-start', 'rename-finish', 'delete-node', 'new-file', 'new-folder'],
  setup(props, { emit }) {
    const isActive = (path: string) => {
      const { useWorkspaceStore } = require('@/stores/workspace')
      return useWorkspaceStore().activeFilePath === path
    }

    const handleClick = () => {
      emit('open', props.node)
    }

    const handleContextMenu = (e: MouseEvent) => {
      emit('context-menu', e, props.node)
    }

    const finishRename = () => {
      emit('rename-finish', props.node)
    }

    return () => {
      const isFolder = props.node.type === 'folder'
      const isOpen = props.node.is_open
      const isRenaming = props.renamingPath === props.node.path
      const active = isActive(props.node.path)

      return h('div', { class: 'tree-node' }, [
        h('div', {
          class: ['node-row', { active, folder: isFolder, open: isOpen }],
          style: { paddingLeft: `${props.depth * 16 + 8}px` },
          onClick: handleClick,
          onContextmenu: handleContextMenu,
        }, [
          h('span', { class: 'chevron' }, isFolder ? (isOpen ? '▼' : '▶') : ''),
          h('span', { class: 'node-icon' }, isFolder ? (isOpen ? '📂' : '📁') : '📄'),
          isRenaming
            ? h('input', {
                class: 'rename-input',
                value: props.renameValue,
                autofocus: true,
                onBlur: finishRename,
                onKeyup: (e: KeyboardEvent) => {
                  if (e.key === 'Enter') finishRename()
                  if (e.key === 'Escape') emit('rename-finish', props.node)
                },
              })
            : h('span', { class: 'node-name' }, props.node.name),
        ]),
        isFolder && isOpen && props.node.children && props.node.children.length
          ? h('div', { class: 'node-children' },
              props.node.children.map((child) =>
                h(TreeNode, {
                  key: child.id,
                  node: child,
                  depth: props.depth + 1,
                  renamingPath: props.renamingPath,
                  renameValue: props.renameValue,
                  onOpen: (n: FileNode) => emit('open', n),
                  onToggle: (n: FileNode) => emit('toggle', n.path),
                  onContextmenu: (e: MouseEvent, n: FileNode) => emit('context-menu', e, n),
                  onRenameStart: (n: FileNode) => emit('rename-start', n),
                  onRenameFinish: (n: FileNode) => emit('rename-finish', n),
                  onDeleteNode: (n: FileNode) => emit('delete-node', n),
                  onNewFile: (p: string) => emit('new-file', p),
                  onNewFolder: (p: string) => emit('new-folder', p),
                })
              )
            )
          : null,
      ])
    }
  },
})

export default {}
</script>

<style scoped>
.file-tree-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  font-size: var(--font-size-sm);
}

.panel-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-sm) var(--space-md);
  border-bottom: 1px solid var(--color-border-subtle);
}

.toolbar-left {
  display: flex;
  gap: 2px;
}

.tool-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
  font-size: 14px;
  transition: all var(--motion-fast);

  &:hover {
    background: var(--color-background-hover);
    color: var(--color-text-primary);
  }
}

.new-input {
  padding: var(--space-sm) var(--space-md);

  input {
    width: 100%;
    padding: 4px 8px;
    background: var(--color-background-secondary);
    border: 1px solid var(--color-border-focus);
    border-radius: var(--radius-sm);
    font-size: 13px;
    outline: none;
  }
}

.tree-container {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: var(--space-xs) 0;
}

.tree-node {
  user-select: none;
}

.node-row {
  display: flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding-right: var(--space-md);
  cursor: pointer;
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  margin-right: 4px;
  transition: background var(--motion-fast);

  &:hover {
    background: var(--color-background-hover);
  }

  &.active {
    background: var(--color-accent-soft);
    color: var(--color-accent-primary);
  }
}

.chevron {
  width: 14px;
  font-size: 9px;
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}

.node-icon {
  font-size: 14px;
  flex-shrink: 0;
}

.node-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-primary);
}

.rename-input {
  flex: 1;
  padding: 2px 4px;
  font-size: 13px;
  background: var(--color-surface-primary);
  border: 1px solid var(--color-border-focus);
  border-radius: var(--radius-sm);
  outline: none;
}

.context-menu {
  position: fixed;
  z-index: var(--z-dropdown);
  background: var(--color-surface-elevated);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  padding: 4px;
  box-shadow: var(--shadow-lg);
  min-width: 140px;
  display: flex;
  flex-direction: column;

  button {
    text-align: left;
    padding: 6px 10px;
    font-size: 13px;
    color: var(--color-text-primary);
    border-radius: var(--radius-sm);

    &:hover {
      background: var(--color-background-hover);
    }

    &.danger {
      color: var(--color-error);
    }
  }
}
</style>
