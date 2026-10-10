import { onMounted, onUnmounted } from 'vue'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { isDesktop } from '@/services/platform/desktop'
import { useWorkspaceStore } from '@/stores/workspace'
import { useEditorStore } from '@/stores/editor'

interface WorkspaceChange { vault_id: string; revision: number; paths: string[] }

/** Desktop events with a cheap snapshot fallback; Web polls only while visible. */
export function useWorkspaceRefresh() {
  const workspace = useWorkspaceStore(), editor = useEditorStore()
  const desktop = isDesktop()
  let stopped = false, running = false, queued = false, forceNext = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let unlisten: UnlistenFn | undefined
  async function refresh(force = false) {
    forceNext ||= force
    if (stopped) return
    if (running) { queued = true; return }
    clearTimeout(timer)
    running = true
    const explicit = forceNext; forceNext = false
    try {
      if (document.visibilityState !== 'hidden' && workspace.hasVault) {
        const vault = workspace.vaultId, path = editor.currentFilePath
        const ownsDocument = editor.captureDocument(), navigation = workspace.navigationRevision
        await workspace.refreshFileTree(explicit)
        if (!stopped && vault === workspace.vaultId && path && ownsDocument()
          && navigation === workspace.navigationRevision && !workspace.isLoading) {
          const current = workspace.findNodeByPath(workspace.fileTree, path)
          if (!current) editor.setExternalChanged()
          else await editor.checkExternalFile(current.content_hash)
        }
      }
    } catch { /* Store retains the snapshot and exposes a retryable refresh error. */ }
    finally {
      running = false
      if (!stopped) {
        if (queued) { queued = false; void refresh() }
        else timer = setTimeout(() => { void refresh() }, desktop ? 30000 : 5000)
      }
    }
  }
  const resume = () => { if (document.visibilityState !== 'hidden') void refresh(true) }
  onMounted(async () => {
    window.addEventListener('focus', resume)
    document.addEventListener('visibilitychange', resume)
    if (desktop) {
      try {
        const stop = await listen<WorkspaceChange>('workspace-changed', event => {
          if (event.payload.vault_id === workspace.vaultId) void refresh()
        })
        if (stopped) stop(); else unlisten = stop
      } catch { /* 30s snapshot fallback still works if event subscription fails. */ }
    }
    if (!stopped) void refresh()
  })
  onUnmounted(() => {
    stopped = true; clearTimeout(timer); unlisten?.()
    window.removeEventListener('focus', resume)
    document.removeEventListener('visibilitychange', resume)
  })
}
