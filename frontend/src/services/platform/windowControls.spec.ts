import { beforeEach, describe, expect, it, vi } from 'vitest'

const native = vi.hoisted(() => ({
  enabled: false,
  minimize: vi.fn(),
  toggleMaximize: vi.fn(),
  close: vi.fn(),
}))

vi.mock('@tauri-apps/api/core', () => ({ isTauri: () => native.enabled, invoke: vi.fn() }))
vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({
    minimize: native.minimize,
    toggleMaximize: native.toggleMaximize,
    close: native.close,
  }),
}))

import { minimizeWindow, requestWindowClose, toggleMaximizeWindow } from './windowControls'

beforeEach(() => {
  native.enabled = false
  native.minimize.mockReset()
  native.toggleMaximize.mockReset()
  native.close.mockReset()
})

describe('桌面窗口控制', () => {
  it('Web 模式不模拟窗口操作', async () => {
    await minimizeWindow()
    await toggleMaximizeWindow()
    await requestWindowClose()
    expect(native.minimize).not.toHaveBeenCalled()
    expect(native.toggleMaximize).not.toHaveBeenCalled()
    expect(native.close).not.toHaveBeenCalled()
  })

  it('桌面模式调用当前 Tauri 窗口', async () => {
    native.enabled = true
    await minimizeWindow()
    await toggleMaximizeWindow()
    await requestWindowClose()
    expect(native.minimize).toHaveBeenCalledOnce()
    expect(native.toggleMaximize).toHaveBeenCalledOnce()
    expect(native.close).toHaveBeenCalledOnce()
  })
})
