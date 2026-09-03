import type {
  ApiNote,
  ApiWorkspaceEntry,
  ApiWorkspaceInfo,
  ApiWorkspaceSnapshot,
  FileNode,
  OperationResponse,
} from '@/contracts'
import apiClient from './apiClient'
import * as noteService from './noteService'

/** Web 联调只连接 AI Core 配置的单一 Vault；多 Vault 选择由 Tauri Host 接管。 */
export interface VaultInfo {
  vault_id: string
  path: string
  name: string
}

let cachedTree: FileNode[] | null = null
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
  if (!noteId) throw new Error(`笔记尚未建立后端索引：${path}`)
  return noteId
}

export async function getWorkspaceInfo(): Promise<ApiWorkspaceInfo> {
  return apiClient.get('/api/workspace')
}

export async function getRecentVaults(): Promise<VaultInfo[]> {
  const workspace = await getWorkspaceInfo()
  return [{ vault_id: workspace.vault_id, path: workspace.path, name: workspace.name }]
}

export async function openVault(path: string): Promise<VaultInfo> {
  const snapshot = await apiClient.post<ApiWorkspaceSnapshot>('/api/workspace/open', { path })
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
  const entries = await apiClient.get<ApiWorkspaceEntry[]>('/api/workspace/tree')
  return cacheEntries(entries)
}

export async function getFileTree(): Promise<FileNode[]> {
  return cachedTree ?? refreshTree()
}

export async function readFileContent(filePath: string): Promise<string> {
  const note = await noteService.getNote(await requireNoteId(filePath))
  return note.markdown
}

/** Resolve the backend note identity already associated with a workspace path. */
export async function getNoteId(filePath: string): Promise<string> {
  return requireNoteId(filePath)
}

export async function saveFileContent(filePath: string, content: string): Promise<void> {
  await noteService.updateNote(await requireNoteId(filePath), { markdown: content })
}

export async function createFile(
  folderPath: string,
  name: string,
  content = '',
): Promise<FileNode> {
  const title = name.replace(/\.md$/i, '')
  const note = await noteService.createNote({
    title,
    folder: relativePath(folderPath),
    markdown: content,
  })
  return nodeFromNote(note)
}

export async function createFolder(parentPath: string, name: string): Promise<FileNode> {
  const entry = await apiClient.post<ApiWorkspaceEntry>('/api/workspace/folders', {
    parent: relativePath(parentPath),
    name,
  })
  return toFileNode(entry)
}

export async function renameFile(oldPath: string, newName: string): Promise<void> {
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
  const source = normalizePublicPath(sourcePath)
  if (typeByPath.get(source) !== 'file') {
    throw new Error('当前阶段只支持移动笔记文件。')
  }
  await noteService.moveNote(await requireNoteId(source), relativePath(targetPath))
  await refreshTree()
}
