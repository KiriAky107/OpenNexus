/** 桌面窗口操作集中于此边界；Web 页面不会显示或模拟原生窗口行为。 */
import { getCurrentWindow } from '@tauri-apps/api/window'
import { isDesktop } from './desktop'

export async function minimizeWindow() {
  if (isDesktop()) await getCurrentWindow().minimize()
}

export async function toggleMaximizeWindow() {
  if (isDesktop()) await getCurrentWindow().toggleMaximize()
}

/** Track native resize/snap changes as well as the title-bar button. */
export async function observeWindowMaximized(changed: (maximized: boolean) => void): Promise<() => void> {
  if (!isDesktop()) return () => undefined
  const window = getCurrentWindow()
  let stopped = false, revision = 0
  const refresh = async () => {
    const current = ++revision
    const maximized = await window.isMaximized()
    if (!stopped && current === revision) changed(maximized)
  }
  const unlisten = await window.onResized(() => { void refresh().catch(() => undefined) })
  try { await refresh() } catch (error) { stopped = true; unlisten(); throw error }
  return () => { stopped = true; unlisten() }
}

export async function requestWindowClose() {
  // close 触发 Rust CloseRequested；Host 会要求前端先保存，再由 lifecycle destroy。
  if (isDesktop()) await getCurrentWindow().close()
}
