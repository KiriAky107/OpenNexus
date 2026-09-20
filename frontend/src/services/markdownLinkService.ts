import router from '@/router'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import { noteOutline } from '@/features/workspace/outline'
import { hostInvoke, isDesktop } from '@/services/platform/desktop'

const externalSchemes = /^(https?:|mailto:|tel:)/i
const unsafeSchemes = /^(javascript:|data:|file:|vbscript:)/i

function decode(value: string) {
  try { return decodeURIComponent(value) } catch { return value }
}

export function resolveNoteLink(href: string, currentPath: string | null): { path: string; fragment: string } | null {
  if (externalSchemes.test(href) || unsafeSchemes.test(href)) return null
  const [rawPath, rawFragment = ''] = href.split('#', 2)
  if (!rawPath && currentPath) return { path: currentPath, fragment: decode(rawFragment) }
  const pathPart = decode(rawPath.split('?')[0] ?? '').replace(/\\/g, '/')
  if (!pathPart || !/\.md$/i.test(pathPart)) return null
  const base = pathPart.startsWith('/') ? [] : (currentPath ?? '/').split('/').slice(0, -1)
  const parts = [...base, ...pathPart.split('/')]
  const normalized: string[] = []
  for (const part of parts) {
    if (!part || part === '.') continue
    if (part === '..') { if (!normalized.length) return null; normalized.pop() }
    else normalized.push(part)
  }
  return { path: `/${normalized.join('/')}`, fragment: decode(rawFragment) }
}

function slug(value: string) {
  return value.trim().toLowerCase().replace(/[\s]+/g, '-').replace(/[^\p{L}\p{N}\-_]/gu, '')
}

async function openExternal(href: string) {
  if (isDesktop()) await hostInvoke('open_external_url', { url: href })
  else window.open(href, '_blank', 'noopener,noreferrer')
}

export async function navigateMarkdownHref(href: string): Promise<boolean> {
  const value = href.trim()
  if (!value || unsafeSchemes.test(value)) return false
  if (externalSchemes.test(value)) { await openExternal(value); return true }

  if (/^\/?#\/media(?:\?|$)/.test(value)) {
    const route = value.replace(/^\/?#/, '')
    await router.push(route)
    return true
  }

  const editor = useEditorStore()
  const target = resolveNoteLink(value, editor.currentFilePath)
  if (!target) return false
  if (target.path !== editor.currentFilePath) await editor.loadFile(target.path)
  const workspace = useWorkspaceStore()
  workspace.openFile(target.path)
  await router.push('/workspace')
  if (target.fragment) {
    const wanted = slug(target.fragment)
    const heading = noteOutline(editor.content).find(item => item.title === target.fragment || slug(item.title) === wanted)
    if (heading) editor.jumpToHeading(heading.index, heading.offset)
  }
  return true
}
