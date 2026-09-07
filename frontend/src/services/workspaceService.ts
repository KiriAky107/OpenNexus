import type {
  ApiNote,
  ApiWorkspaceEntry,
  ApiWorkspaceInfo,
  ApiWorkspaceSnapshot,
  FileNode,
  OperationResponse,
} from '@/contracts'
import apiClient from './apiClient'
import { t } from '@/i18n'
import * as noteService from './noteService'
import { splitNoteMetadata } from '@/utils/noteMetadata'
import { contentHash, hostInvoke, isDesktop, nativePath, nativeTree, type HostDocument, type HostEntry, type HostVault } from './platform/desktop'

/** Web 联调只连接 AI Core 配置的单一 Vault；多 Vault 选择由 Tauri Host 接管。 */
export interface VaultInfo {
  vault_id: string
  path: string
  name: string
}

let cachedTree: FileNode[] | null = null
let treeRequestVersion = 0
const noteIdByPath = new Map<string, string>()
const typeByPath = new Map<string, FileNode['type']>()

function normalizePublicPath(path: string): string {
  const normalized = path.replace(/\\/g, '/').replace(/^\/+|\/+$/g, '')
  return normalized ? `/${normalized}` : '/'
}

function relativePath(path: string): string {
  return normalizePublicPath(path).replace(/^\//, '')
}

function toFileNode(entry: ApiWorkspaceEntry): FileNode {
  const path = normalizePublicPath(entry.path)
  const node: FileNode = {
    id: entry.entry_id,
    note_id: entry.note_id ?? undefined,
    name: entry.name,
    path,
    type: entry.type,
    is_open: false,
    children: entry.type === 'folder' ? entry.children.map(toFileNode) : undefined,
  }
  typeByPath.set(path, entry.type)
  if (entry.note_id) noteIdByPath.set(path, entry.note_id)
  return node
}

function cacheEntries(entries: ApiWorkspaceEntry[]): FileNode[] {
  noteIdByPath.clear()
  typeByPath.clear()
  cachedTree = entries.map(toFileNode)
  return cachedTree
}

function nodeFromNote(note: ApiNote): FileNode {
  const path = normalizePublicPath(note.file_path)
  noteIdByPath.set(path, note.note_id)
  typeByPath.set(path, 'file')
  return {
    id: note.note_id,
    note_id: note.note_id,
    name: path.split('/').at(-1) || note.title,
    path,
    type: 'file',
  }
}

async function requireNoteId(filePath: string): Promise<string> {
  const path = normalizePublicPath(filePath)
  let noteId = noteIdByPath.get(path)
  if (!noteId) {
    await refreshTree()
    noteId = noteIdByPath.get(path)
  }
  if (!noteId) throw new Error(`${t('笔记尚未建立后端索引：', 'The note has not been indexed by the backend: ')}${path}`)
  return noteId
}

export async function getWorkspaceInfo(): Promise<ApiWorkspaceInfo> {
  if (isDesktop()) {
    const vault = (await getRecentVaults())[0]
    if (!vault) throw new Error('VAULT_NOT_OPEN')
    const entries = await hostInvoke<HostEntry[]>('workspace_tree')
    return { ...vault, file_count: entries.length, indexed_note_count: 0, requires_refresh: false }
  }
  return apiClient.get('/api/workspace', { timeoutMs: 15000 })
}

export async function getRecentVaults(): Promise<VaultInfo[]> {
  if (isDesktop()) return hostInvoke<HostVault[]>('workspace_recent')
  const workspace = await getWorkspaceInfo()
  return [{ vault_id: workspace.vault_id, path: workspace.path, name: workspace.name }]
}

export async function openVault(path: string): Promise<VaultInfo> {
  if (isDesktop()) {
    // 空路径只表示用户点击“选择目录”；最近列表只能重开 Host 已持久化授权的路径。
    const vault = path
      ? await hostInvoke<HostVault>('workspace_open', { path })
      : await hostInvoke<HostVault | null>('workspace_choose')
    if (!vault) throw new Error(t('已取消选择', 'Selection cancelled'))
    cachedTree = null; noteIdByPath.clear(); typeByPath.clear(); treeRequestVersion++
    return vault
  }
  treeRequestVersion++
  const snapshot = await apiClient.post<ApiWorkspaceSnapshot>('/api/workspace/open', { path }, { timeoutMs: 15000 })
  cacheEntries(snapshot.items)
  return {
    vault_id: snapshot.workspace.vault_id,
    path: snapshot.workspace.path,
    name: snapshot.workspace.name,
  }
}

export async function createVault(path: string, name: string): Promise<VaultInfo> {
  // Web 模式不能创建任意本地目录；路径匹配时等价于初始化后端配置的 Vault。
  void name
  return openVault(path)
}

export async function refreshTree(): Promise<FileNode[]> {
  const version = ++treeRequestVersion
  if (isDesktop()) {
    const entries = await hostInvoke<HostEntry[]>('workspace_tree')
    if (version !== treeRequestVersion) return cachedTree ?? []
    noteIdByPath.clear(); typeByPath.clear()
    for (const entry of entries) { if (!entry.is_folder) noteIdByPath.set(`/${entry.path}`, entry.file_id); typeByPath.set(`/${entry.path}`, entry.is_folder ? 'folder' : 'file') }
    cachedTree = nativeTree(entries)
    return cachedTree
  }
  const entries = await apiClient.get<ApiWorkspaceEntry[]>('/api/workspace/tree', { timeoutMs: 10000 })
  if (version !== treeRequestVersion) return cachedTree ?? []
  return cacheEntries(entries)
}

export async function getFileTree(): Promise<FileNode[]> {
  return cachedTree ?? refreshTree()
}

export async function readFileContent(filePath: string): Promise<string> {
  if (isDesktop()) return (await hostInvoke<HostDocument>('workspace_read', { path: nativePath(filePath) })).content
  const note = await noteService.getNote(await requireNoteId(filePath))
  return note.markdown
}

/** Resolve the backend note identity already associated with a workspace path. */
export async function getNoteId(filePath: string): Promise<string> {
  return requireNoteId(filePath)
}

export async function saveFileContent(filePath: string, content: string, expectedContent?: string): Promise<void> {
  if (isDesktop()) {
    if (expectedContent === undefined) throw new Error('EXPECTED_REVISION_REQUIRED')
    await hostInvoke('workspace_write', { path: nativePath(filePath), expected: await contentHash(expectedContent), content })
    return
  }
  const metadata = splitNoteMetadata(content)
  const expectedHash = expectedContent === undefined ? undefined : Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(expectedContent)))).map(byte => byte.toString(16).padStart(2, '0')).join('')
  await noteService.updateNote(await requireNoteId(filePath), {
    markdown: content,
    ...(expectedHash ? { expected_content_hash: expectedHash } : {}),
    // Explicit [] clears the index; absent tags retain API-managed tags.
    ...(metadata?.hasTags ? { tags: metadata.tags } : {}),
  })
}

export async function createFile(
  folderPath: string,
  name: string,
  content = '',
): Promise<FileNode> {
  if (isDesktop()) {
    const path = [nativePath(folderPath), name.endsWith('.md') ? name : `${name}.md`].filter(Boolean).join('/')
    const entry = await hostInvoke<HostEntry>('workspace_write', { path, expected: '', content })
    noteIdByPath.set(`/${path}`, entry.file_id)
    return { id: entry.file_id, note_id: entry.file_id, path: `/${path}`, name: path.split('/').at(-1)!, type: 'file' }
  }
  const title = name.replace(/\.md$/i, '')
  const note = await noteService.createNote({
    title,
    folder: relativePath(folderPath),
    markdown: content,
  })
  return nodeFromNote(note)
}

export async function createFolder(parentPath: string, name: string): Promise<FileNode> {
  if (isDesktop()) {
    const path = [nativePath(parentPath), name].filter(Boolean).join('/')
    await hostInvoke('workspace_mkdir', { path })
    return { id: `folder:/${path}`, path: `/${path}`, name, type: 'folder', children: [] }
  }
  const entry = await apiClient.post<ApiWorkspaceEntry>('/api/workspace/folders', {
    parent: relativePath(parentPath),
    name,
  })
  return toFileNode(entry)
}

export async function renameFile(oldPath: string, newName: string): Promise<void> {
  if (isDesktop()) {
    const path = nativePath(oldPath)
    const document = await hostInvoke<HostDocument>('workspace_read', { path })
    await hostInvoke('workspace_rename', { path, destination: [...path.split('/').slice(0, -1), newName].join('/'), expected: document.hash })
    await refreshTree(); return
  }
  const path = normalizePublicPath(oldPath)
  if (typeByPath.get(path) === 'folder') {
    await apiClient.post('/api/workspace/folders/rename', {
      path: relativePath(path),
      new_name: newName,
    })
  } else {
    await noteService.renameNote(await requireNoteId(path), newName)
  }
  await refreshTree()
}

export async function deleteFile(pathValue: string): Promise<void> {
  if (isDesktop()) {
    const path = nativePath(pathValue)
    const document = await hostInvoke<HostDocument>('workspace_read', { path })
    await hostInvoke('workspace_delete', { path, expected: document.hash })
    await refreshTree(); return
  }
  const path = normalizePublicPath(pathValue)
  if (typeByPath.get(path) === 'folder') {
    await apiClient.post<OperationResponse>('/api/workspace/folders/delete', {
      path: relativePath(path),
    })
  } else {
    await noteService.deleteNote(await requireNoteId(path))
  }
  await refreshTree()
}

export async function moveFile(sourcePath: string, targetPath: string): Promise<void> {
  if (isDesktop()) {
    const path = nativePath(sourcePath)
    const document = await hostInvoke<HostDocument>('workspace_read', { path })
    await hostInvoke('workspace_rename', { path, destination: [nativePath(targetPath), path.split('/').at(-1)].filter(Boolean).join('/'), expected: document.hash })
    await refreshTree(); return
  }
  const source = normalizePublicPath(sourcePath)
  if (typeByPath.get(source) !== 'file') {
    throw new Error(t('当前阶段只支持移动笔记文件。', 'Only note files can be moved at this stage.'))
  }
  await noteService.moveNote(await requireNoteId(source), relativePath(targetPath))
  await refreshTree()
}
