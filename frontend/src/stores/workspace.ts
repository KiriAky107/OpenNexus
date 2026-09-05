import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { FileNode } from '@/contracts'
import * as workspaceService from '@/services/workspaceService'

export const useWorkspaceStore = defineStore('workspace', () => {
  const vaultPath = ref('')
  const vaultId = ref('')
  const vaultName = ref('')
  const fileTree = ref<FileNode[]>([])
  const openFiles = ref<string[]>([])
  const activeFilePath = ref<string | null>(null)
  const isLoading = ref(false)
  const hasVault = ref(false)
  const recentVaults = ref<workspaceService.VaultInfo[]>([])

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
    if (!openFiles.value.includes(path)) {
      openFiles.value.push(path)
    }
    activeFilePath.value = path
  }

  function closeFile(path: string) {
    const idx = openFiles.value.indexOf(path)
    if (idx > -1) {
      openFiles.value.splice(idx, 1)
      if (activeFilePath.value === path) {
        activeFilePath.value = openFiles.value[Math.min(idx, openFiles.value.length - 1)] || null
      }
    }
  }

  function setActiveFile(path: string | null) {
    activeFilePath.value = path
  }

  async function loadRecentVaults() {
    recentVaults.value = await workspaceService.getRecentVaults()
  }

  /** 重新拉取文件树。插件命令返回 refresh:workspace 时需要。 */
  async function refreshFileTree() {
    if (!hasVault.value) return
    fileTree.value = await workspaceService.getFileTree()
  }

  async function openVault(path: string) {
    isLoading.value = true
    try {
      const info = await workspaceService.openVault(path)
      vaultPath.value = info.path
      vaultId.value = info.vault_id
      vaultName.value = info.name
      fileTree.value = await workspaceService.getFileTree()
      hasVault.value = true
      localStorage.setItem('last-vault-path', info.path)
    } finally {
      isLoading.value = false
    }
  }

  async function createVault(path: string, name: string) {
    isLoading.value = true
    try {
      const info = await workspaceService.createVault(path, name)
      vaultPath.value = info.path
      vaultId.value = info.vault_id
      vaultName.value = info.name
      fileTree.value = await workspaceService.getFileTree()
      hasVault.value = true
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
    const node = findNodeByPath(fileTree.value, oldPath)
    if (!node) return
    // 文件夹重命名必须同步改写所有后代、标签页和当前文件路径。
    const updateNodePath = (current: FileNode) => {
      if (current.path === oldPath) current.name = newName
      if (current.path === oldPath || current.path.startsWith(`${oldPath}/`)) {
        current.path = `${newPath}${current.path.slice(oldPath.length)}`
      }
      current.children?.forEach(updateNodePath)
    }
    updateNodePath(node)
    openFiles.value = openFiles.value.map((path) =>
      path === oldPath || path.startsWith(`${oldPath}/`) ? `${newPath}${path.slice(oldPath.length)}` : path
    )
    if (activeFilePath.value && (activeFilePath.value === oldPath || activeFilePath.value.startsWith(`${oldPath}/`))) {
      activeFilePath.value = `${newPath}${activeFilePath.value.slice(oldPath.length)}`
    }
  }

  function closePath(path: string) {
    const activeWasRemoved = Boolean(activeFilePath.value && (activeFilePath.value === path || activeFilePath.value.startsWith(`${path}/`)))
    openFiles.value = openFiles.value.filter((openPath) => openPath !== path && !openPath.startsWith(`${path}/`))
    if (activeWasRemoved) activeFilePath.value = openFiles.value.at(-1) ?? null
    return activeWasRemoved
  }

  return {
    vaultPath,
    vaultId,
    vaultName,
    fileTree,
    openFiles,
    activeFilePath,
    activeFile,
    isLoading,
    hasVault,
    recentVaults,
    toggleFolder,
    openFile,
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
