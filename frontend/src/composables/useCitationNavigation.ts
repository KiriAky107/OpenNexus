import { useRouter } from 'vue-router'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'

/**
 * 引用目标。字段用 unknown 是因为 Agent 事件流里拿到的是
 * Record<string, unknown>（SSE 原始 data），不保证结构完整。
 */
export interface CitationTarget {
  file_path?: unknown
  block_id?: unknown
}

export interface CitationNavigationDeps {
  loadFile: (filePath: string) => Promise<void>
  openFile: (filePath: string) => void
  highlightBlock: (blockId: string) => void
  navigate: (path: string) => Promise<unknown> | unknown
}

function asPath(value: unknown): string {
  return typeof value === 'string' && value.trim() !== '' ? value : ''
}

/**
 * 定位到引用对应的笔记块。
 *
 * 调用顺序不能改：editor store 的 loadFile 在末尾会把 highlightBlockId 清空，
 * 所以必须等它 resolve 之后再 highlightBlock，否则高亮会被自己冲掉。
 * loadFile 失败（例如当前文件有未解决的保存冲突）时直接抛出，
 * 不跳转，避免把用户从未保存的编辑器里弹走。
 */
export async function navigateToCitation(
  target: CitationTarget,
  deps: CitationNavigationDeps,
): Promise<void> {
  const filePath = asPath(target.file_path)
  if (!filePath) throw new Error('该引用缺少文件路径，无法定位到笔记。')

  await deps.loadFile(filePath)
  deps.openFile(filePath)

  const blockId = asPath(target.block_id)
  if (blockId) deps.highlightBlock(blockId)

  await deps.navigate('/workspace')
}

/** 组件里用的封装：绑定真实的 store 与路由。 */
export function useCitationNavigation() {
  const router = useRouter()
  const editorStore = useEditorStore()
  const workspaceStore = useWorkspaceStore()

  return {
    openCitation: (target: CitationTarget) =>
      navigateToCitation(target, {
        loadFile: (filePath) => editorStore.loadFile(filePath),
        openFile: (filePath) => workspaceStore.openFile(filePath),
        highlightBlock: (blockId) => editorStore.highlightBlock(blockId),
        navigate: (path) => router.push(path),
      }),
  }
}
