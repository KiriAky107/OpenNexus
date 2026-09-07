/** 桌面窗口操作集中于此边界；Web 页面不会显示或模拟原生窗口行为。 */
import { getCurrentWindow } from '@tauri-apps/api/window'
import { isDesktop } from './desktop'

export async function minimizeWindow() {
  if (isDesktop()) await getCurrentWindow().minimize()
}

export async function toggleMaximizeWindow() {
  if (isDesktop()) await getCurrentWindow().toggleMaximize()
}

export async function requestWindowClose() {
  // close 触发 Rust CloseRequested；Host 会要求前端先保存，再由 lifecycle destroy。
  if (isDesktop()) await getCurrentWindow().close()
}
