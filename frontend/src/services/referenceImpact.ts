import type { FileNode } from '@/contracts'
import { scanVaultReferences, type VaultReference } from './vaultReferences'
import { workspaceDocumentType } from './workspaceDocuments'

export interface ReferenceImpact {
  incoming: VaultReference[]
  ambiguous: VaultReference[]
  outgoing: VaultReference[]
}

export interface ReferenceDocument { path: string; content: string }

export function documentPaths(tree: FileNode[]): string[] {
  const paths: string[] = []
  const walk = (nodes: FileNode[]) => nodes.forEach(node => {
    if (node.type === 'folder') walk(node.children ?? [])
    else paths.push(node.path)
  })
  walk(tree)
  return paths
}

/** A path can change while its note_id stays the same. Never guess an ambiguous link. */
export function inspectReferenceImpact(
  documents: ReferenceDocument[], paths: Set<string>, oldPath: string,
): ReferenceImpact {
  const incoming: VaultReference[] = [], ambiguous: VaultReference[] = [], outgoing: VaultReference[] = []
  const affected = (path: string) => path === oldPath || path.startsWith(`${oldPath}/`)
  for (const document of documents) {
    for (const ref of scanVaultReferences(document.path, document.content, paths)) {
      if (ref.target && affected(ref.target)) incoming.push(ref)
      else if (ref.kind === 'wiki' && ref.status === 'ambiguous' &&
        (ref.raw === oldPath.slice(1) || `${ref.raw}.md` === oldPath.split('/').at(-1))) ambiguous.push(ref)
      if (affected(document.path) && ref.status === 'resolved' && ref.target && !affected(ref.target)) outgoing.push(ref)
    }
  }
  return { incoming, ambiguous, outgoing }
}

export async function inspectWorkspaceReferenceImpact(
  tree: FileNode[], oldPath: string, read: (path: string) => Promise<string>,
): Promise<ReferenceImpact> {
  const paths = documentPaths(tree)
  const documents: ReferenceDocument[] = []
  for (const path of paths) {
    if (!['markdown', 'canvas'].includes(workspaceDocumentType(path))) continue
    documents.push({ path, content: await read(path) })
  }
  return inspectReferenceImpact(documents, new Set(paths), oldPath)
}
