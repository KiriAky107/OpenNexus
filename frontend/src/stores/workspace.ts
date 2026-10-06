import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { FileNode } from '@/contracts'
import * as workspaceService from '@/services/workspaceService'
import { normalizeWorkspacePath } from '@/services/workspacePaths'

export const useWorkspaceStore = defineStore('workspace', () => {
  const vaultPath = ref('')
  const vaultId = ref('')
  const vaultName = ref('')
  const fileTree = ref<FileNode[]>([])
  const openFiles = ref<string[]>([])
  const recentFiles = ref<string[]>([])
  const activeFilePath = ref<string | null>(null)
  const activeFolderPath = ref<string | null>(null)
  const isLoading = ref(false)
  const hasVault = ref(false)
  const recentVaults = ref<workspaceService.VaultInfo[]>([])
  const treeRefreshError = ref<string | null>(null)
  // Navigation intent invalidates document reads even before a new tab is committed.
  const navigationRevision = ref(0)
  let refreshSequence = 0

  const activeFile = computed(() => {
    if (!activeFilePath.value) return null
    return findNodeByPath(fileTree.value, activeFilePath.value)
  })

  function findNodeByPath(nodes: FileNode[], path: string): FileNode | null {
    for (const node of nodes) {
      if (node.path === path) return node
      if (node.children) {
        const found = findNodeByPath(node.children, path)
        if (found) return found
      }
    }
    return null
  }

  function toggleFolder(path: string) {
    const node = findNodeByPath(fileTree.value, path)
    if (node && node.type === 'folder') {
      node.is_open = !node.is_open
    }
  }

  function openFile(path: string) {
    navigationRevision.value++
    path = normalizeWorkspacePath(path)
    activeFolderPath.value = null
    if (!openFiles.value.includes(path)) {
      openFiles.value.push(path)
    }
    activeFilePath.value = path
    rememberRecentFile(path)
  }

  function rememberRecentFile(path: string) {
    path = normalizeWorkspacePath(path)
    const node = findNodeByPath(fileTree.value, path)
    if (!node || node.type !== 'file') return
    recentFiles.value = [path, ...recentFiles.value.filter(previous => previous !== path)].slice(0, 20)
    try { localStorage.setItem(`workspace-recent-files:${vaultId.value}`, JSON.stringify(recentFiles.value)) } catch { /* session-only storage */ }
  }

  function restoreRecentFiles() {
    let saved: unknown = []
    try { saved = JSON.parse(localStorage.getItem(`workspace-recent-files:${vaultId.value}`) ?? '[]') } catch { /* invalid prior state */ }
    recentFiles.value = Array.isArray(saved) ? saved.filter((path): path is string =>
      typeof path === 'string' && findNodeByPath(fileTree.value, path)?.type === 'file').slice(0, 20) : []
  }

  function closeFile(path: string) {
    navigationRevision.value++
    path = normalizeWorkspacePath(path)
    const idx = openFiles.value.indexOf(path)
    if (idx > -1) {
      openFiles.value.splice(idx, 1)
      if (activeFilePath.value === path) {
        activeFilePath.value = openFiles.value[Math.min(idx, openFiles.value.length - 1)] || null
      }
    }
  }

  function setActiveFile(path: string | null) {
    navigationRevision.value++
    activeFolderPath.value = null
    activeFilePath.value = path === null ? null : normalizeWorkspacePath(path)
  }
  function selectFolder(path: string) {
    navigationRevision.value++
    activeFolderPath.value = path
    activeFilePath.value = null
    try { localStorage.setItem(`workspace-folder:${vaultId.value}`, path) } catch { /* session state */ }
  }
  function restoreFolder() {
    activeFolderPath.value = null
    try { const path = localStorage.getItem(`workspace-folder:${vaultId.value}`); if (path === '/' || (path && findNodeByPath(fileTree.value,path)?.type === 'folder')) activeFolderPath.value = path } catch { /* session state */ }
  }

  async function loadRecentVaults() {
    recentVaults.value = await workspaceService.getRecentVaults()
  }

  /** 重新拉取文件树。插件命令返回 refresh:workspace 时需要。 */
  async function refreshFileTree(force = true) {
    if (!hasVault.value) return
    const sequence = ++refreshSequence
    const path = vaultPath.value
    try {
      const fresh = await workspaceService.refreshTree(force)
      if (sequence !== refreshSequence || path !== vaultPath.value || !hasVault.value) return
      const open = new Map<string, boolean>()
      const collect = (nodes: FileNode[]) => nodes.forEach(node => { if (node.type === 'folder') open.set(node.path, !!node.is_open); if (node.children) collect(node.children) })
      collect(fileTree.value)
      const restore = (nodes: FileNode[]) => nodes.forEach(node => { if (node.type === 'folder') node.is_open = open.get(node.path) ?? false; if (node.children) restore(node.children) })
      restore(fresh)
      // 避免在每次背景检查时重绘未更改的树。
      if (JSON.stringify(fresh) !== JSON.stringify(fileTree.value)) fileTree.value = fresh
      const existingRecent = recentFiles.value.filter(recent => findNodeByPath(fileTree.value, recent)?.type === 'file')
      if (existingRecent.length !== recentFiles.value.length) {
        recentFiles.value = existingRecent
        try { localStorage.setItem(`workspace-recent-files:${vaultId.value}`, JSON.stringify(existingRecent)) } catch { /* session-only storage */ }
      }
      treeRefreshError.value = null
    } catch (error) {
      if (sequence === refreshSequence) treeRefreshError.value = error instanceof Error ? error.message : '文件树刷新失败'
      throw error
    }
  }

  async function openVault(path: string) {
    navigationRevision.value++
    refreshSequence++
    isLoading.value = true
    try {
      const info = await workspaceService.openVault(path)
      const tree = await workspaceService.getFileTree()
      vaultPath.value = info.path
      vaultId.value = info.vault_id
      vaultName.value = info.name
      fileTree.value = tree
      restoreRecentFiles()
      openFiles.value = []
      activeFilePath.value = null
      hasVault.value = true
      restoreFolder()
      localStorage.setItem('last-vault-path', info.path)
    } finally {
      isLoading.value = false
    }
  }

  async function createVault(path: string, name: string) {
    navigationRevision.value++
    refreshSequence++
    isLoading.value = true
    try {
      const info = await workspaceService.createVault(path, name)
      const tree = await workspaceService.getFileTree()
      vaultPath.value = info.path
      vaultId.value = info.vault_id
      vaultName.value = info.name
      fileTree.value = tree
      restoreRecentFiles()
      openFiles.value = []
      activeFilePath.value = null
      hasVault.value = true
      restoreFolder()
      localStorage.setItem('last-vault-path', info.path)
    } finally {
      isLoading.value = false
    }
  }

  function addFileToTree(parentPath: string, file: FileNode) {
    if (parentPath === '/' || parentPath === '') {
      fileTree.value.push(file)
      return
    }
    const parent = findNodeByPath(fileTree.value, parentPath)
    if (parent?.children) {
      parent.children.push(file)
      parent.is_open = true
    }
  }

  function removeFromTree(path: string) {
    function remove(nodes: FileNode[]): boolean {
      for (let i = 0; i < nodes.length; i++) {
        if (nodes[i].path === path) {
          nodes.splice(i, 1)
          return true
        }
        if (nodes[i].children && remove(nodes[i].children!)) return true
      }
      return false
    }
    remove(fileTree.value)
  }

  function renamePath(oldPath: string, newPath: string, newName: string) {
    navigationRevision.value++
    const node = findNodeByPath(fileTree.value, oldPath)
    // 文件夹重命名必须同步改写所有后代、标签页和当前文件路径。
    const updateNodePath = (current: FileNode) => {
      if (current.path === oldPath) current.name = newName
      if (current.path === oldPath || current.path.startsWith(`${oldPath}/`)) {
        current.path = `${newPath}${current.path.slice(oldPath.length)}`
      }
      current.children?.forEach(updateNodePath)
    }
    if (node) updateNodePath(node)
    openFiles.value = openFiles.value.map((path) =>
      path === oldPath || path.startsWith(`${oldPath}/`) ? `${newPath}${path.slice(oldPath.length)}` : path
    )
    if (activeFilePath.value && (activeFilePath.value === oldPath || activeFilePath.value.startsWith(`${oldPath}/`))) {
      activeFilePath.value = `${newPath}${activeFilePath.value.slice(oldPath.length)}`
    }
    recentFiles.value = recentFiles.value.map(path => path === oldPath || path.startsWith(`${oldPath}/`) ? `${newPath}${path.slice(oldPath.length)}` : path)
    if (activeFolderPath.value && (activeFolderPath.value === oldPath || activeFolderPath.value.startsWith(`${oldPath}/`))) selectFolder(`${newPath}${activeFolderPath.value.slice(oldPath.length)}`)
    try { localStorage.setItem(`workspace-recent-files:${vaultId.value}`, JSON.stringify(recentFiles.value)) } catch { /* session-only storage */ }
  }

  function closePath(path: string) {
    navigationRevision.value++
    if (activeFolderPath.value === path || activeFolderPath.value?.startsWith(`${path}/`)) selectFolder('/')
    const activeWasRemoved = Boolean(activeFilePath.value && (activeFilePath.value === path || activeFilePath.value.startsWith(`${path}/`)))
    openFiles.value = openFiles.value.filter((openPath) => openPath !== path && !openPath.startsWith(`${path}/`))
    if (activeWasRemoved) activeFilePath.value = openFiles.value.at(-1) ?? null
    recentFiles.value = recentFiles.value.filter(recent => recent !== path && !recent.startsWith(`${path}/`))
    try { localStorage.setItem(`workspace-recent-files:${vaultId.value}`, JSON.stringify(recentFiles.value)) } catch { /* session-only storage */ }
    return activeWasRemoved
  }

  return {
    vaultPath,
    vaultId,
    vaultName,
    fileTree,
    openFiles,
    recentFiles,
    activeFilePath,
    activeFolderPath,
    selectFolder,
    activeFile,
    isLoading,
    hasVault,
    recentVaults,
    treeRefreshError,
    navigationRevision,
    findNodeByPath,
    toggleFolder,
    openFile,
    rememberRecentFile,
    closeFile,
    setActiveFile,
    loadRecentVaults,
    refreshFileTree,
    openVault,
    createVault,
    addFileToTree,
    removeFromTree,
    renamePath,
    closePath,
  }
})
