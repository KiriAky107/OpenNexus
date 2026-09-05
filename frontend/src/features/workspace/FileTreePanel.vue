<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { noteOutline } from './outline'
import { useRouter } from 'vue-router'
import type { FileNode } from '@/contracts'
import * as workspaceService from '@/services/workspaceService'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import FileTreeNode from './FileTreeNode.vue'
import { Document, DocumentAdd, FolderAdd, ArrowRight } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'
import { t } from '@/i18n'

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
const searchVisible = ref(false)
const activeTab = ref<'files' | 'outline'>('files')
function switchTab(tab: 'files' | 'outline') { activeTab.value = tab; closeContextMenu() }
function navigateTabs(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  switchTab(event.key === 'Home' ? 'files' : event.key === 'End' ? 'outline' : activeTab.value === 'files' ? 'outline' : 'files')
  const parent = (event.target as HTMLElement).parentElement
  void nextTick(() => parent?.querySelector<HTMLButtonElement>('[aria-selected="true"]')?.focus())
}
const searchQuery = ref('')
const searchFocused = ref(false)
const createInput = ref<HTMLInputElement | null>(null)
const createError = ref('')
const creating = ref(false)
const outline = computed(() => noteOutline(editorStore.content))
const collapsedHeadings = ref(new Set<number>())
const visibleHeadings = computed(() => {
  let hiddenBelow = 7
  return outline.value.filter(heading => {
    if (heading.level > hiddenBelow) return false
    hiddenBelow = collapsedHeadings.value.has(heading.index) ? heading.level : 7
    return true
  })
})
const hasChildren = (index: number) => {
  const position = outline.value.findIndex(heading => heading.index === index)
  return (outline.value[position + 1]?.level ?? 0) > (outline.value[position]?.level ?? 6)
}
function toggleHeading(index: number) {
  const next = new Set(collapsedHeadings.value)
  if (next.has(index)) next.delete(index); else next.add(index)
  collapsedHeadings.value = next
}
watch(() => editorStore.content, () => { collapsedHeadings.value = new Set() })
const filteredTree = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase()
  if (!query) return workspaceStore.fileTree
  const filter = (nodes: FileNode[]): FileNode[] => nodes.flatMap(node => {
    if (node.name.toLocaleLowerCase().includes(query)) return [{ ...node, is_open: true }]
    const children = filter(node.children ?? [])
    return children.length ? [{ ...node, children, is_open: true }] : []
  })
  return filter(workspaceStore.fileTree)
})
function expandAllFiles() {
  const expand = (nodes: FileNode[]) => nodes.forEach(node => {
    if (node.type === 'folder') { node.is_open = true; expand(node.children ?? []) }
  })
  expand(workspaceStore.fileTree)
}
let lastScrollTop = 0
function revealSearch(event: WheelEvent) {
  if (activeTab.value !== 'files') return
  if (event.deltaY < 0) searchVisible.value = true
  else if (event.deltaY > 0 && !searchFocused.value && !searchQuery.value) searchVisible.value = false
}
function onTreeScroll(event: Event) {
  const top = (event.target as HTMLElement).scrollTop
  if (top < lastScrollTop) searchVisible.value = true
  else if (top > lastScrollTop && !searchFocused.value && !searchQuery.value) searchVisible.value = false
  lastScrollTop = top
  closeContextMenu()
}

watch(() => workspaceStore.activeFilePath, (path) => {
  if (!path) return
  selectedTreePath.value = path
  selectedFolderPath.value = containingFolder(path)
})

function beginCreate(type: 'file' | 'folder', parent = '/') {
  if (creating.value) return
  closeContextMenu()
  createError.value = ''
  newItemType.value = type
  newItemName.value = ''
  parentPath.value = parent
  void nextTick(() => createInput.value?.focus())
}

async function createItem() {
  const rawName = newItemName.value.trim()
  if (!rawName || !newItemType.value) return
  if (creating.value) return
  if (/[\\/]/.test(rawName) || ['.', '..'].includes(rawName)) { createError.value = t('请输入有效名称，不要包含路径分隔符', 'Enter a name without path separators'); return }
  creating.value = true
  createError.value = ''
  try {
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
  } catch (error) { createError.value = error instanceof Error ? error.message : t('创建失败', 'Creation failed') }
  finally { creating.value = false }
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
  contextMenuPosition.value = { x: Math.max(8, Math.min(event.clientX, window.innerWidth - 170)), y: Math.max(8, Math.min(event.clientY, window.innerHeight - 170)) }
}

function closeContextMenu() { contextTarget.value = null }

async function renameTarget() {
  const node = contextTarget.value
  if (!node) return
  const newName = window.prompt(t('新名称', 'New name'), node.name)?.trim()
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
  if (!window.confirm(`${t('确定要删除', 'Delete')} “${node.name}”?`)) return closeContextMenu()
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
  <section class="file-tree-panel" @click="closeContextMenu" @keydown.esc="closeContextMenu" @wheel.passive="revealSearch">
    <div class="workspace-tabs" role="tablist" :aria-label="t('工作区导航', 'Workspace navigation')" @keydown="navigateTabs">
      <button id="workspace-files-tab" role="tab" aria-controls="workspace-files-panel" :aria-selected="activeTab === 'files'" :tabindex="activeTab === 'files' ? 0 : -1" @click="switchTab('files')">{{ t('文件', 'Files') }}</button>
      <button id="workspace-outline-tab" role="tab" aria-controls="workspace-outline-panel" :aria-selected="activeTab === 'outline'" :tabindex="activeTab === 'outline' ? 0 : -1" @click="switchTab('outline')">{{ t('大纲', 'Outline') }}</button>
    </div>
    <div v-show="activeTab === 'files'" id="workspace-files-panel" class="files-panel" role="tabpanel" aria-labelledby="workspace-files-tab">
    <div class="toolbar">
      <button type="button" :title="t('新建笔记', 'New note')" :aria-label="t('新建笔记', 'New note')" @click.stop="beginCreate('file', selectedFolderPath)"><AppIcon :icon="DocumentAdd" /></button>
      <button type="button" :title="t('新建文件夹', 'New folder')" :aria-label="t('新建文件夹', 'New folder')" @click.stop="beginCreate('folder', selectedFolderPath)"><AppIcon :icon="FolderAdd" /></button>
      <button type="button" :aria-label="t('搜索文件', 'Search files')" :aria-expanded="searchVisible" @click="searchVisible = !searchVisible">{{ t('搜索', 'Search') }}</button>
      <button type="button" :aria-label="t('全部展开文件夹', 'Expand all folders')" @click="expandAllFiles">{{ t('全部展开', 'Expand all') }}</button>
    </div>
    <div v-if="searchVisible || searchQuery || searchFocused" class="file-search">
      <input v-model="searchQuery" type="search" :placeholder="t('搜索文件或文件夹…', 'Search files or folders…')" :aria-label="t('搜索文件或文件夹', 'Search files or folders')" @focus="searchFocused = true" @blur="searchFocused = false" />
    </div>
    <form v-if="newItemType" class="new-item" @submit.prevent="createItem">
      <input ref="createInput" v-model="newItemName" :disabled="creating" :placeholder="newItemType === 'file' ? t('笔记名称', 'Note name') : t('文件夹名称', 'Folder name')" />
      <button type="submit" :disabled="creating">{{ t('创建', 'Create') }}</button>
      <button type="button" :disabled="creating" @click="newItemType = null">{{ t('取消', 'Cancel') }}</button>
    </form>
    <p v-if="createError" class="create-error" role="alert">{{ createError }}</p>
    <div class="tree" @scroll.passive="onTreeScroll" @contextmenu.self="openContextMenu($event, { id: 'root', name: '/', path: '/', type: 'folder' })">
      <FileTreeNode v-for="node in filteredTree" :key="node.id" :node="node"
        :active-path="selectedTreePath" @open="openNode" @context-menu="openContextMenu" />
      <p v-if="searchQuery && !filteredTree.length" class="subtle">{{ t('没有匹配的文件', 'No matching files') }}</p>
    </div>
    </div>
    <div v-show="activeTab === 'outline'" id="workspace-outline-panel" class="outline-panel" role="tabpanel" aria-labelledby="workspace-outline-tab">
      <div class="outline-document">
        <span class="outline-document-icon"><AppIcon :icon="Document" :size="18" /></span>
        <div class="outline-document-info">
          <p class="outline-filename" :title="editorStore.currentFilePath ?? ''">{{ editorStore.currentFilePath?.split('/').pop() ?? t('未打开笔记', 'No note open') }}</p>
          <span class="outline-meta">{{ t('文档目录', 'Contents') }} · {{ outline.length }} {{ t('个标题', 'headings') }}</span>
        </div>
      </div>
      <div v-if="outline.length" class="outline-controls">
        <span>{{ t('目录', 'Contents') }}</span>
        <button :title="t('展开全部标题', 'Expand all headings')" @click="collapsedHeadings = new Set()">{{ t('全部展开', 'Expand all') }}</button>
      </div>
      <nav class="outline-list" :aria-label="t('当前笔记大纲', 'Current note outline')">
        <div v-for="heading in visibleHeadings" :key="heading.index" class="outline-row" :data-level="heading.level" :class="{ 'is-selected': editorStore.headingRequest?.path === editorStore.currentFilePath && editorStore.headingRequest?.index === heading.index, 'is-nested': heading.level > 1 }" :style="{ marginLeft: `${(heading.level - 1) * 10}px` }">
          <button v-if="hasChildren(heading.index)" class="outline-toggle" :aria-label="t('折叠或展开标题', 'Toggle heading')" :aria-expanded="!collapsedHeadings.has(heading.index)" @click="toggleHeading(heading.index)"><AppIcon :icon="ArrowRight" :size="10" /></button>
          <span v-else class="outline-spacer" />
          <button class="outline-title" :title="heading.title" :aria-current="editorStore.headingRequest?.path === editorStore.currentFilePath && editorStore.headingRequest?.index === heading.index ? 'location' : undefined" @click="editorStore.jumpToHeading(heading.index, heading.offset)"><span class="outline-text">{{ heading.title }}</span><span class="outline-level" aria-hidden="true">H{{ heading.level }}</span></button>
        </div>
        <div v-if="!outline.length" class="outline-empty"><AppIcon :icon="Document" :size="28" /><strong>{{ t('还没有目录', 'No outline yet') }}</strong><p>{{ t('在笔记中添加标题，即可在这里浏览和跳转。', 'Add headings to your note to navigate here.') }}</p></div>
      </nav>
    </div>
    <Teleport to="body">
      <div v-if="contextTarget" class="context-menu"
        :style="{ left: `${contextMenuPosition.x}px`, top: `${contextMenuPosition.y}px` }" @click.stop>
        <button @click="beginCreate('file', selectedFolderPath)">{{ t('新建文件', 'New file') }}</button>
        <button @click="beginCreate('folder', selectedFolderPath)">{{ t('新建文件夹', 'New folder') }}</button>
        <button v-if="contextTarget.path !== '/'" @click="renameTarget">{{ t('重命名', 'Rename') }}</button>
        <button v-if="contextTarget.path !== '/'" class="danger" @click="deleteTarget">{{ t('删除', 'Delete') }}</button>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.file-tree-panel { height: 100%; min-height: 0; display: flex; flex-direction: column; background: var(--color-surface-secondary); color: var(--color-text-primary); }
.workspace-tabs { display: flex; flex-shrink: 0; gap: 4px; padding: 8px; border-bottom: 1px solid var(--color-border-default); background: var(--color-background-secondary); }
.workspace-tabs button { flex: 1; min-height: 34px; font-weight: 600; color: var(--color-text-secondary); }
.workspace-tabs button[aria-selected="true"] { background: var(--color-accent-soft); color: var(--color-accent-primary); box-shadow: inset 0 -2px var(--color-accent-primary); }
.files-panel { display: flex; flex: 1; min-height: 0; flex-direction: column; }
.file-tree-panel button:focus-visible, .context-menu button:focus-visible { outline: 2px solid var(--color-border-focus); outline-offset: -2px; }
.file-search { padding: 8px; }
.file-search input { width: 100%; box-sizing: border-box; padding: 6px 8px; border: 1px solid var(--color-border-default); border-radius: var(--radius-sm); background: var(--color-surface-primary); color: var(--color-text-primary); }
.create-error { padding: 8px; color: var(--color-error); }
.outline-panel { flex: 1; min-height: 0; overflow: auto; }
.outline-document { display: flex; align-items: center; gap: 10px; margin: 12px 10px; padding: 12px 10px; border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); background: var(--color-surface-primary); box-shadow: var(--shadow-sm); }
.outline-document-icon { display: grid; place-items: center; flex-shrink: 0; width: 32px; height: 36px; border-radius: var(--radius-sm); background: var(--color-accent-soft); color: var(--color-accent-primary); }
.outline-document-info { min-width: 0; }
.outline-filename { margin: 0 0 4px; padding: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--color-text-primary); font-size: var(--font-size-sm); font-weight: 600; border: 0; }
.outline-meta { font-size: var(--font-size-xs); color: var(--color-text-secondary); }
.outline-controls { display: flex; align-items: center; justify-content: space-between; padding: 4px 12px 8px; color: var(--color-text-secondary); font-size: var(--font-size-xs); }
.outline-controls button { color: var(--color-accent-primary); font-size: inherit; }
.outline-list { padding: 0 10px 16px; }
.outline-row { position: relative; display: flex; align-items: center; min-height: 34px; margin-bottom: 2px; padding: 0 6px 0 2px; border: 1px solid transparent; border-radius: var(--radius-sm); transition: background-color var(--motion-fast); }
.outline-row:hover { background: var(--color-background-hover); }
.outline-row.is-selected { background: var(--color-accent-soft); box-shadow: inset 2px 0 var(--color-accent-primary); }
.outline-spacer, .outline-toggle { width: 18px; flex-shrink: 0; }
.outline-row .outline-toggle { display: grid; place-items: center; padding: 4px 0; color: var(--color-text-secondary); }
.outline-toggle[aria-expanded="true"] :deep(svg) { transform: rotate(90deg); }
.outline-row .outline-title { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; padding: 6px 2px; text-align: left; background: transparent; }
.outline-level { flex-shrink: 0; color: var(--color-text-tertiary); font: 400 10px/18px var(--font-ui-mono); }
.outline-text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: var(--font-size-sm); line-height: 20px; }
.is-selected .outline-text { color: var(--color-accent-primary); }
.is-selected .outline-level { color: var(--color-accent-primary); }

.outline-row[data-level="1"] .outline-text { font-size: 15px; font-weight: 700; }
.outline-row[data-level="2"] .outline-text { font-size: 14px; font-weight: 600; }
.outline-row[data-level="3"] .outline-text { font-size: 13px; font-weight: 500; }
.outline-row[data-level="4"] .outline-text { font-size: 13px; font-weight: 400; }
.outline-row[data-level="5"] .outline-text, .outline-row[data-level="6"] .outline-text { font-size: 12px; font-weight: 400; }
.outline-empty { display: grid; justify-items: center; gap: 10px; padding: 32px 16px; text-align: center; color: var(--color-text-secondary); }
.outline-empty strong { color: var(--color-text-primary); font-size: var(--font-size-sm); }
.outline-empty p { margin: 0; font-size: var(--font-size-xs); line-height: 1.7; }
.toolbar { display: flex; gap: var(--space-xs); padding: var(--space-sm); border-bottom: 1px solid var(--color-border-subtle); }
button { border: 0; border-radius: var(--radius-sm); padding: var(--space-xs) var(--space-sm); background: transparent; color: inherit; cursor: pointer; }
button:hover { background: var(--color-background-hover); }
.new-item { display: flex; gap: var(--space-xs); padding: var(--space-sm); }
.new-item input { min-width: 0; flex: 1; padding: 6px 8px; border: 1px solid var(--color-border-default); border-radius: var(--radius-sm); background: var(--color-surface-primary); color: var(--color-text-primary); }
.file-search input:focus, .new-item input:focus { outline: 2px solid var(--color-border-focus); outline-offset: 1px; }
.tree { padding: var(--space-xs); flex: 1; min-height: 80px; overflow: auto; }
.context-menu { position: fixed; z-index: 1000; display: grid; min-width: 130px; padding: var(--space-xs); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-background-primary); box-shadow: var(--shadow-md); }
.context-menu button { text-align: left; }
.context-menu .danger { color: var(--color-error); }
</style>
