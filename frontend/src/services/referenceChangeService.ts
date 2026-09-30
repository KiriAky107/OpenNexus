import * as workspace from './workspaceService'
import { contentHash } from './platform/desktop'
import { VaultLinkIndex } from './vaultLinkIndex'
import { documentPaths } from './referenceImpact'
import { resolveVaultReference, rewritePathReferences, type VaultReference } from './vaultReferences'
import type { FileNode } from '@/contracts'

export interface ReferenceChangePlan {
  oldPath: string; newPath: string | null; expectedHash?: string; expectedEntries?: Record<string,string>
  updates: Array<{ path: string; destination: string; before: string; after: string; count: number }>
  pending: VaultReference[]
  affected: VaultReference[]
  tree: FileNode[]
}
export async function prepareReferenceChange(oldPath: string, newPath: string | null): Promise<ReferenceChangePlan> {
  if (newPath && (resolveVaultReference('/reference.md', newPath, true) !== newPath || newPath === oldPath || newPath.startsWith(`${oldPath}/`))) throw new Error('INVALID_DESTINATION_PATH')
  const tree = await workspace.refreshTree(), paths = new Set(documentPaths(tree)), index = new VaultLinkIndex()
  await index.update(tree, workspace.readFileContent)
  if (index.errors.length) throw new Error(`无法读取引用来源：${index.errors.map(item => item.path).join('、')}`)
  const moved = (path: string) => path === oldPath || path.startsWith(`${oldPath}/`)
  const affected = index.references.filter(ref => ref.target && moved(ref.target))
  const pending: VaultReference[] = [], updates: ReferenceChangePlan['updates'] = []
  for (const document of index.documents.values()) {
    if (!newPath) continue
    const result = rewritePathReferences(document.path, document.content, oldPath, newPath, paths)
    if (result.content !== document.content) updates.push({ path: document.path, destination: moved(document.path) ? `${newPath}${document.path.slice(oldPath.length)}` : document.path, before: document.content, after: result.content, count: result.changed.length })
    pending.push(...result.pending.filter(ref => moved(document.path) || (ref.kind === 'wiki' && (ref.raw.split('#')[0] === oldPath.split('/').at(-1)?.replace(/\.md$/i, '') || oldPath.endsWith(`/${ref.raw}`)))))
  }
  const content = index.documents.get(oldPath)?.content
  const find = (nodes: FileNode[]): FileNode | undefined => nodes.find(node => node.path === oldPath) ?? nodes.flatMap(node => node.children ?? []).map(node => find([node])).find(Boolean)
  const expectedHash = content === undefined ? find(tree)?.content_hash : await contentHash(content)
  const node = find(tree), expectedEntries: Record<string,string> = {}
  const collect = (nodes: FileNode[]) => nodes.forEach(item => { if (item.type === 'folder') { expectedEntries[item.path.slice(oldPath.length+1)] = ''; collect(item.children ?? []) } else { if (!item.content_hash) throw new Error(`修订缺失：${item.path}`); expectedEntries[item.path.slice(oldPath.length+1)] = item.content_hash } })
  if (node?.type === 'folder') collect(node.children ?? [])
  return { oldPath, newPath, expectedHash, expectedEntries: node?.type === 'folder' ? expectedEntries : undefined, updates, pending, affected, tree }
}
export interface ReferenceChangeResult { changed: string[]; pending: string[]; failures: Array<{ path: string; error: string }> }
/** Each write compares the reviewed snapshot. Partial results are explicit; never overwrite a conflict. */
export async function executeReferenceChange(plan: ReferenceChangePlan, update: boolean, mutate: (expectedHash?: string) => Promise<void>, assertScope: () => void): Promise<ReferenceChangeResult> {
  assertScope()
  if (update) {
    for (const item of plan.updates) {
      if (await workspace.readFileContent(item.path) !== item.before) throw new Error(`引用来源已改变，请重新预览：${item.path}`)
      assertScope()
    }
  }
  await mutate(plan.expectedHash)
  const result: ReferenceChangeResult = { changed: [], pending: [...new Set((update ? plan.pending : [...plan.affected, ...plan.pending]).map(ref => ref.source))], failures: [] }
  if (update) for (const item of plan.updates) {
    try {
      assertScope()
      await workspace.saveFileContent(item.destination, item.after, item.before)
      result.changed.push(item.destination)
    } catch (error) { result.failures.push({ path: item.destination, error: String(error) }) }
  }
  return result
}
