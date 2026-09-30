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
import { EMPTY_CANVAS, validateCanvasContent, workspaceDocumentType } from './workspaceDocuments'
import { resolveVaultReference } from './vaultReferences'
import { inspectWorkspaceReferenceImpact, type ReferenceImpact } from './referenceImpact'

interface CanvasDocument { path: string; content: string; content_hash: string }

export interface WorkspaceAsset {
  asset_id: string
  path: string
  content_hash: string
  media_type: string
  size: number
  original_name: string
}

export type WorkspaceAssetSource = 'paste' | 'drop' | 'upload'

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
    content_hash: entry.content_hash ?? undefined,
    updated_at: entry.updated_at ?? undefined,
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
    return { ...vault, file_count: entries.filter(entry => !entry.is_folder && !entry.deleted).length, indexed_note_count: 0, requires_refresh: false }
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
    for (const entry of entries) { if (!entry.is_folder && /\.md$/i.test(entry.path)) noteIdByPath.set(`/${entry.path}`, entry.file_id); typeByPath.set(`/${entry.path}`, entry.is_folder ? 'folder' : 'file') }
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

export async function previewPathChange(path: string): Promise<ReferenceImpact> {
  return inspectWorkspaceReferenceImpact(await refreshTree(), path, readFileContent)
}

export async function readFileContent(filePath: string): Promise<string> {
  if (workspaceDocumentType(filePath) === 'image') throw new Error('IMAGE_USE_BINARY_ASSET_API')
  if (workspaceDocumentType(filePath) === 'unsupported') throw new Error('UNSUPPORTED_DOCUMENT_TYPE')
  if (isDesktop()) return (await hostInvoke<HostDocument>('workspace_read', { path: nativePath(filePath) })).content
  if (workspaceDocumentType(filePath) === 'canvas') return (await apiClient.get<CanvasDocument>('/api/workspace/canvas', { params: { path: filePath } })).content
  const note = await noteService.getNote(await requireNoteId(filePath))
  return note.markdown
}

/** 将 Vault 根路径转换为相对当前笔记的可移植 Markdown 引用。 */
export function workspaceAssetReference(notePath: string, assetPath: string): string {
  const noteParts = relativePath(notePath).split('/').filter(Boolean)
  noteParts.pop()
  return `${'../'.repeat(noteParts.length)}${relativePath(assetPath)}`
}

/** 将笔记内的相对附件引用还原为 Vault 根路径。 */
export function resolveWorkspaceAssetPath(notePath: string, reference: string): string | null {
  const target = resolveVaultReference(notePath, reference)
  return target && isWorkspaceImage(target) && !target.toLowerCase().split('/').includes('opennexus-records') ? target.replace(/^\//, '') : null
}

export function isWorkspaceImage(path: string): boolean { return /\.(png|jpe?g|gif|webp)$/i.test(path) }

export function isMutableWorkspaceImage(path: string): boolean {
  return isWorkspaceImage(path) && !/^\/?attachments\/[0-9a-f]{2}\/[0-9a-f]{64}\.(png|jpg|gif|webp)$/i.test(path)
}

async function imageRevision(path: string): Promise<string> {
  if (isDesktop()) {
    const entries = await hostInvoke<HostEntry[]>('workspace_tree')
    const entry = entries.find(item => !item.deleted && item.path === nativePath(path))
    if (!entry?.hash) throw new Error('IMAGE_REVISION_UNAVAILABLE')
    return entry.hash
  }
  const bytes = await (await loadWorkspaceImage(relativePath(path))).arrayBuffer()
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)))
    .map(byte => byte.toString(16).padStart(2, '0')).join('')
}

export async function storeWorkspaceImage(
  file: Blob & { name?: string }, source: WorkspaceAssetSource,
  notePath: string, noteId: string | null,
): Promise<WorkspaceAsset & { reference: string }> {
  const asset = await apiClient.postBinary<WorkspaceAsset>('/api/workspace/assets', file, { 'Content-Type': 'application/octet-stream' }, {
    filename: file.name || 'image', note_id: noteId || '', note_path: notePath, source,
  })
  return { ...asset, reference: workspaceAssetReference(notePath, asset.path) }
}

export async function loadWorkspaceImage(path: string, notePath = '', noteId: string | null = null): Promise<Blob> {
  const response = await apiClient.get<Response>('/api/workspace/assets/content', { params: { path, note_path: notePath, note_id: noteId || '' } })
  return response.blob()
}

/** 解析已与工作空间路径关联的后端笔记标识。 */
export async function getNoteId(filePath: string): Promise<string> {
  return requireNoteId(filePath)
}

export async function saveFileContent(filePath: string, content: string, expectedContent?: string): Promise<void> {
  if (workspaceDocumentType(filePath) !== 'markdown' && workspaceDocumentType(filePath) !== 'canvas') throw new Error('UNSUPPORTED_DOCUMENT_TYPE')
  if (workspaceDocumentType(filePath) === 'canvas') validateCanvasContent(content)
  if (isDesktop()) {
    if (expectedContent === undefined) throw new Error('EXPECTED_REVISION_REQUIRED')
    await hostInvoke('workspace_write', { path: nativePath(filePath), expected: await contentHash(expectedContent), content })
    return
  }
  if (workspaceDocumentType(filePath) === 'canvas') {
    if (expectedContent === undefined) throw new Error('EXPECTED_REVISION_REQUIRED')
    await apiClient.put('/api/workspace/canvas', { path: filePath, expected_content_hash: await contentHash(expectedContent), content })
    return
  }
  const metadata = splitNoteMetadata(content)
  const expectedHash = expectedContent === undefined ? undefined : Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(expectedContent)))).map(byte => byte.toString(16).padStart(2, '0')).join('')
  await noteService.updateNote(await requireNoteId(filePath), {
    markdown: content,
    ...(expectedHash ? { expected_content_hash: expectedHash } : {}),
    // 显式[]清除索引；缺失的标签保留 API 管理的标签。
    ...(metadata?.hasTags ? { tags: metadata.tags } : {}),
  })
}

export async function createFile(
  folderPath: string,
  name: string,
  content = '',
): Promise<FileNode> {
  const fileName = /\.(md|canvas)$/i.test(name) ? name : `${name}.md`
  const kind = workspaceDocumentType(fileName)
  if (kind !== 'markdown' && kind !== 'canvas') throw new Error('UNSUPPORTED_DOCUMENT_TYPE')
  const initialContent = kind === 'canvas' ? (content || EMPTY_CANVAS) : content
  if (kind === 'canvas') validateCanvasContent(initialContent)
  if (isDesktop()) {
    const path = [nativePath(folderPath), fileName].filter(Boolean).join('/')
    const entry = await hostInvoke<HostEntry>('workspace_write', { path, expected: '', content: initialContent })
    if (kind === 'markdown') noteIdByPath.set(`/${path}`, entry.file_id)
    return { id: entry.file_id, note_id: kind === 'markdown' ? entry.file_id : undefined, path: `/${path}`, name: path.split('/').at(-1)!, type: 'file' }
  }
  if (kind === 'canvas') {
    const path = [relativePath(folderPath), fileName].filter(Boolean).join('/')
    await apiClient.put<CanvasDocument>('/api/workspace/canvas', { path, expected_content_hash: '', content: initialContent })
    return { id: `canvas:${path}`, path: `/${path}`, name: fileName, type: 'file' }
  }
  const title = fileName.replace(/\.md$/i, '')
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

export async function renameFile(oldPath: string, newName: string, expectedHash?: string, expectedEntries?: Record<string,string>): Promise<void> {
  if (expectedEntries) {
    const destination = `${oldPath.slice(0, oldPath.lastIndexOf('/') + 1)}${newName}`
    if (isDesktop()) await hostInvoke('workspace_folder_operation', { path: nativePath(oldPath), destination: nativePath(destination), kind: 'rename', expected: expectedEntries })
    else await apiClient.post('/api/workspace/folders/rename', { path: relativePath(oldPath), new_name: newName, expected_entries: expectedEntries })
    await refreshTree(); return
  }
  if (workspaceDocumentType(oldPath) === 'image') {
    if (!isMutableWorkspaceImage(oldPath)) throw new Error('IMAGE_ASSET_IMMUTABLE')
    const path = normalizePublicPath(oldPath)
    const destination = `${path.slice(0, path.lastIndexOf('/') + 1)}${newName}`
    if (workspaceDocumentType(destination) !== 'image' || path.slice(path.lastIndexOf('.')).toLowerCase() !== destination.slice(destination.lastIndexOf('.')).toLowerCase()) throw new Error('IMAGE_EXTENSION_MISMATCH')
    const expected = expectedHash ?? await imageRevision(path)
    if (isDesktop()) await hostInvoke('workspace_rename', { path: nativePath(path), destination: nativePath(destination), expected })
    else await apiClient.post('/api/workspace/assets/move', { path, destination, expected_content_hash: expected })
    await refreshTree(); return
  }
  if (isDesktop()) {
    const path = nativePath(oldPath)
    const document = await hostInvoke<HostDocument>('workspace_read', { path })
    await hostInvoke('workspace_rename', { path, destination: [...path.split('/').slice(0, -1), newName].join('/'), expected: expectedHash ?? document.hash })
    await refreshTree(); return
  }
  const path = normalizePublicPath(oldPath)
  if (workspaceDocumentType(path) === 'canvas') {
    const document = await apiClient.get<CanvasDocument>('/api/workspace/canvas', { params: { path } })
    const destination = `${path.slice(0, path.lastIndexOf('/') + 1)}${newName}`
    await apiClient.post('/api/workspace/canvas/move', { path, destination, expected_content_hash: expectedHash ?? document.content_hash })
    await refreshTree(); return
  }
  if (typeByPath.get(path) === 'folder') {
    await apiClient.post('/api/workspace/folders/rename', {
      path: relativePath(path),
      new_name: newName,
    })
  } else {
    await noteService.renameNote(await requireNoteId(path), newName, expectedHash)
  }
  await refreshTree()
}

export async function deleteFile(pathValue: string, expectedHash?: string, expectedEntries?: Record<string,string>): Promise<void> {
  if (expectedEntries) {
    if (isDesktop()) await hostInvoke('workspace_folder_operation', { path: nativePath(pathValue), destination: '', kind: 'delete', expected: expectedEntries })
    else await apiClient.post('/api/workspace/folders/delete', { path: relativePath(pathValue), expected_entries: expectedEntries })
    await refreshTree(); return
  }
  if (workspaceDocumentType(pathValue) === 'image') {
    if (!isMutableWorkspaceImage(pathValue)) throw new Error('IMAGE_ASSET_IMMUTABLE')
    const expected = expectedHash ?? await imageRevision(pathValue)
    if (isDesktop()) await hostInvoke('workspace_delete', { path: nativePath(pathValue), expected })
    else await apiClient.post('/api/workspace/assets/delete', { path: pathValue, expected_content_hash: expected })
    await refreshTree(); return
  }
  if (isDesktop()) {
    const path = nativePath(pathValue)
    const document = await hostInvoke<HostDocument>('workspace_read', { path })
    await hostInvoke('workspace_delete', { path, expected: expectedHash ?? document.hash })
    await refreshTree(); return
  }
  const path = normalizePublicPath(pathValue)
  if (workspaceDocumentType(path) === 'canvas') {
    const document = await apiClient.get<CanvasDocument>('/api/workspace/canvas', { params: { path } })
    await apiClient.post('/api/workspace/canvas/delete', { path, expected_content_hash: expectedHash ?? document.content_hash })
    await refreshTree(); return
  }
  if (typeByPath.get(path) === 'folder') {
    await apiClient.post<OperationResponse>('/api/workspace/folders/delete', {
      path: relativePath(path),
    })
  } else {
    await noteService.deleteNote(await requireNoteId(path), expectedHash)
  }
  await refreshTree()
}

export async function moveFile(sourcePath: string, targetPath: string, review?: { expectedHash: string; reviewed: true }): Promise<void> {
  const impact = review ? {incoming: [], ambiguous: [], outgoing: []} : await previewPathChange(sourcePath)
  const sources = [...new Set([...impact.incoming, ...impact.ambiguous, ...impact.outgoing].map(ref => ref.source))]
  if (sources.length) throw new Error(`REFERENCE_IMPACT_REVIEW_REQUIRED: ${sources.join(', ')}`)
  if (workspaceDocumentType(sourcePath) === 'image') {
    if (!isMutableWorkspaceImage(sourcePath)) throw new Error('IMAGE_ASSET_IMMUTABLE')
    const destination = `${normalizePublicPath(targetPath).replace(/\/$/, '')}/${sourcePath.split('/').at(-1)}`
    const expected = review?.expectedHash ?? await imageRevision(sourcePath)
    if (isDesktop()) await hostInvoke('workspace_rename', { path: nativePath(sourcePath), destination: nativePath(destination), expected })
    else await apiClient.post('/api/workspace/assets/move', { path: sourcePath, destination, expected_content_hash: expected })
    await refreshTree(); return
  }
  if (isDesktop()) {
    const path = nativePath(sourcePath)
    const document = await hostInvoke<HostDocument>('workspace_read', { path })
    await hostInvoke('workspace_rename', { path, destination: [nativePath(targetPath), path.split('/').at(-1)].filter(Boolean).join('/'), expected: review?.expectedHash ?? document.hash })
    await refreshTree(); return
  }
  const source = normalizePublicPath(sourcePath)
  if (workspaceDocumentType(source) === 'canvas') {
    const document = await apiClient.get<CanvasDocument>('/api/workspace/canvas', { params: { path: source } })
    const destination = `${normalizePublicPath(targetPath).replace(/\/$/, '')}/${source.split('/').at(-1)}`
    await apiClient.post('/api/workspace/canvas/move', { path: source, destination, expected_content_hash: review?.expectedHash ?? document.content_hash })
    await refreshTree(); return
  }
  if (typeByPath.get(source) !== 'file') {
    throw new Error(t('当前阶段只支持移动笔记文件。', 'Only note files can be moved at this stage.'))
  }
  await noteService.moveNote(await requireNoteId(source), relativePath(targetPath), review?.expectedHash)
  await refreshTree()
}
