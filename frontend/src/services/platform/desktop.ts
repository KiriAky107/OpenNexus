/** 平台能力集中检测；Web 模式永不回退到虚构的原生数据。 */
import { invoke, isTauri } from '@tauri-apps/api/core'
import type { FileNode } from '@/contracts'

export const isDesktop = () => isTauri()
export interface HostEntry { file_id: string; path: string; hash: string; revision: number; deleted: boolean; is_folder?: boolean }
export interface HostDocument extends HostEntry { content: string }
export interface HostVault { vault_id: string; path: string; name: string }
export interface HostCapabilities { protocol: number; workspace: boolean; core: boolean; sync: boolean; credentials: boolean; extensions: boolean; release: string }

export class DesktopError extends Error {
  constructor(public code: string) { super(code); this.name = 'DesktopError' }
}

export async function hostInvoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  if (!isDesktop()) throw new DesktopError('DESKTOP_UNAVAILABLE')
  try { return await invoke<T>(command, args) }
  catch (error) { throw new DesktopError(typeof error === 'string' ? error : 'HOST_ERROR') }
}

export function nativePath(path: string) { return path.replace(/^\//, '') }

export function nativeTree(entries: HostEntry[]): FileNode[] {
  const roots: FileNode[] = []
  const folders = new Map<string, FileNode>()
  for (const entry of entries) {
    if (entry.deleted) continue
    const parts = entry.path.split('/')
    let children = roots, path = ''
    for (const name of (entry.is_folder ? parts : parts.slice(0, -1))) {
      path += `/${name}`
      let node = folders.get(path)
      if (!node) {
        node = { id: `folder:${path}`, path, name, type: 'folder', children: [] }
        folders.set(path, node); children.push(node)
      }
      children = node.children!
    }
    if (!entry.is_folder) children.push({ id: entry.file_id, note_id: /\.md$/i.test(entry.path) ? entry.file_id : undefined, path: `/${entry.path}`, name: parts.at(-1)!, type: 'file' })
  }
  return roots
}

export async function contentHash(content: string) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(content))))
    .map(value => value.toString(16).padStart(2, '0')).join('')
}
