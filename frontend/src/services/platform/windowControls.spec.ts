import { beforeEach, describe, expect, it, vi } from 'vitest'

const native = vi.hoisted(() => ({
  enabled: false,
  minimize: vi.fn(),
  toggleMaximize: vi.fn(),
  close: vi.fn(),
  isMaximized: vi.fn(),
  onResized: vi.fn(),
}))

vi.mock('@tauri-apps/api/core', () => ({ isTauri: () => native.enabled, invoke: vi.fn() }))
vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({
    minimize: native.minimize,
    toggleMaximize: native.toggleMaximize,
    close: native.close,
    isMaximized: native.isMaximized,
    onResized: native.onResized,
  }),
}))

import { minimizeWindow, observeWindowMaximized, requestWindowClose, toggleMaximizeWindow } from './windowControls'

beforeEach(() => {
  native.enabled = false
  native.minimize.mockReset()
  native.toggleMaximize.mockReset()
  native.close.mockReset()
  native.isMaximized.mockReset().mockResolvedValue(false)
  native.onResized.mockReset()
})

describe('桌面窗口控制', () => {
  it('observes maximize/restore events and stops late callbacks after cleanup', async () => {
    native.enabled = true
    let resize!: () => void
    const unlisten = vi.fn(), changed = vi.fn()
    native.onResized.mockImplementation(async callback => { resize = callback; return unlisten })
    const stop = await observeWindowMaximized(changed)
    expect(changed).toHaveBeenLastCalledWith(false)
    native.isMaximized.mockResolvedValue(true)
    resize()
    await Promise.resolve()
    expect(changed).toHaveBeenLastCalledWith(true)
    stop()
    expect(unlisten).toHaveBeenCalledOnce()
    native.isMaximized.mockResolvedValue(false)
    resize()
    await Promise.resolve()
    expect(changed).toHaveBeenCalledTimes(2)
  })
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
