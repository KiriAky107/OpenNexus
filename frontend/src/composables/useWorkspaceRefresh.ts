import { onMounted, onUnmounted } from 'vue'
import { useWorkspaceStore } from '@/stores/workspace'
import { useEditorStore } from '@/stores/editor'

/** Web fallback until the desktop host supplies filesystem events. No overlapping polls. */
export function useWorkspaceRefresh() {
  const workspace = useWorkspaceStore()
  const editor = useEditorStore()
  let stopped = false
  let running = false
  let timer: ReturnType<typeof setTimeout> | undefined
  async function refresh() {
    if (running || stopped) return
    clearTimeout(timer)
    running = true
    try {
      if (document.visibilityState !== 'hidden' && workspace.hasVault) {
        await workspace.refreshFileTree()
        if (!stopped) {
          if (editor.currentFilePath && editor.currentFilePath === workspace.activeFilePath && !workspace.activeFile) editor.setExternalChanged()
          else await editor.checkExternalFile()
        }
      }
    } catch { /* Keep the existing tree; the store exposes the error and retries. */ }
    finally {
      running = false
      if (!stopped) timer = setTimeout(refresh, 2000)
    }
  }
  onMounted(() => {
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    void refresh()
  })
  onUnmounted(() => {
    stopped = true; clearTimeout(timer)
    window.removeEventListener('focus', refresh)
    document.removeEventListener('visibilitychange', refresh)
  })
}
