import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mobileCapabilities, mobileVault } from './native'

const host = vi.hoisted(() => ({ isTauri: vi.fn(), invoke: vi.fn() }))
vi.mock('@tauri-apps/api/core', () => host)

describe('mobile native boundary', () => {
  beforeEach(() => { vi.resetAllMocks() })

  it('requires the native Host for vault access and never falls back to HTTP', async () => {
    host.isTauri.mockReturnValue(false)
    const fetch = vi.spyOn(globalThis, 'fetch')
    try {
      await expect(mobileCapabilities()).rejects.toThrow('MOBILE_HOST_REQUIRED')
      await expect(mobileVault()).rejects.toThrow('MOBILE_HOST_REQUIRED')
      expect(host.invoke).not.toHaveBeenCalled()
      expect(fetch).not.toHaveBeenCalled()
    } finally { fetch.mockRestore() }
  })

  it('rejects an incompatible native platform instead of treating every Tauri Host as Android', async () => {
    host.isTauri.mockReturnValue(true)
    host.invoke.mockResolvedValue({ protocol: 1, platform: 'windows', workspace: true })
    await expect(mobileCapabilities()).rejects.toThrow('MOBILE_HOST_INCOMPATIBLE')
    expect(host.invoke).toHaveBeenCalledExactlyOnceWith('mobile_capabilities')
  })

  it('rejects unsupported protocol and workspace capability responses', async () => {
    host.isTauri.mockReturnValue(true)
    host.invoke.mockResolvedValueOnce({ protocol: 2, platform: 'android', workspace: true })
    await expect(mobileCapabilities()).rejects.toThrow('MOBILE_HOST_INCOMPATIBLE')
    host.invoke.mockResolvedValueOnce({ protocol: 1, platform: 'android', workspace: false })
    await expect(mobileCapabilities()).rejects.toThrow('MOBILE_HOST_INCOMPATIBLE')
  })
})
