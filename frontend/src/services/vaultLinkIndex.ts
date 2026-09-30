import type { FileNode } from '@/contracts'
import { scanVaultReferences, type VaultReference } from './vaultReferences'
import { workspaceDocumentType } from './workspaceDocuments'

export interface IndexedReference extends VaultReference { context: string }
export interface LinkDocument { path: string; content: string; stamp?: string; references: IndexedReference[] }
export class VaultLinkIndex {
  documents = new Map<string, LinkDocument>()
  errors: Array<{ path: string; error: string }> = []
  private generation = 0
  private pathsKey = ''
  reset() { this.generation++; this.documents.clear(); this.errors = []; this.pathsKey = '' }
  async update(tree: FileNode[], read: (path: string) => Promise<string>) {
    const version = ++this.generation
    const files: FileNode[] = []
    const collect = (nodes: FileNode[]) => nodes.forEach(node => node.type === 'folder' ? collect(node.children ?? []) : files.push(node))
    collect(tree)
    const paths = new Set(files.map(file => file.path)), key = [...paths].sort().join('\n')
    const documents = new Map<string, LinkDocument>(), errors: typeof this.errors = []
    for (let offset = 0; offset < files.length; offset += 8) {
      await Promise.all(files.slice(offset, offset + 8).map(async file => {
        if (!['markdown','canvas'].includes(workspaceDocumentType(file.path))) return
        try {
          const previous = this.documents.get(file.path)
          const stamp = file.content_hash
          const content = stamp && previous?.stamp === stamp ? previous.content : await read(file.path)
          const references = previous?.content === content && this.pathsKey === key ? previous.references : scanVaultReferences(file.path, content, paths).map(ref => ({ ...ref, context: ref.nodeId ? `节点 ${ref.nodeId}: ${ref.raw}` : content.slice(Math.max(0, (ref.start ?? 0) - 65), (ref.end ?? 0) + 90).replace(/\s+/g, ' ').trim() }))
          documents.set(file.path, { path: file.path, content, stamp, references })
        } catch (error) { errors.push({ path: file.path, error: String(error) }) }
      }))
      if (version !== this.generation) return false
    }
    if (version !== this.generation) return false
    this.documents = documents; this.errors = errors; this.pathsKey = key
    return true
  }
  get references() { return [...this.documents.values()].flatMap(document => document.references).sort((a,b) => a.source.localeCompare(b.source) || (a.start ?? 0) - (b.start ?? 0)) }
  backlinks(path: string) { return this.references.filter(ref => ref.status === 'resolved' && ref.target === path) }
  get broken() { return this.references.filter(ref => ['missing','ambiguous','unsafe','unsupported'].includes(ref.status)) }
}
