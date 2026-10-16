import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mobileCapabilities, mobileVault, mobileRequest, mobileSaveExport, mobileExportDelivery, mobileOpenExternal, mobileOnBack, mobileBackground, mobileAppearance } from './native'

const host = vi.hoisted(() => ({ isTauri: vi.fn(), invoke: vi.fn(), back: vi.fn() }))
vi.mock('@tauri-apps/api/core', () => host)
vi.mock('@tauri-apps/api/app', () => ({ onBackButtonPress: host.back }))

describe('mobile native boundary', () => {
  beforeEach(() => { vi.resetAllMocks() })

  it('requires the native Host for vault access and never falls back to HTTP', async () => {
    host.isTauri.mockReturnValue(false)
    const fetch = vi.spyOn(globalThis, 'fetch')
    try {
      await expect(mobileCapabilities()).rejects.toThrow('MOBILE_HOST_REQUIRED')
      await expect(mobileVault()).rejects.toThrow('MOBILE_HOST_REQUIRED')
      await expect(mobileRequest({ kind: 'list_pending', offset: 0, limit: 20 })).rejects.toThrow('MOBILE_HOST_REQUIRED')
      await expect(mobileSaveExport('original')).rejects.toThrow('MOBILE_HOST_REQUIRED')
      await expect(mobileExportDelivery('original')).rejects.toThrow('MOBILE_HOST_REQUIRED')
      await expect(mobileOpenExternal('https://example.com')).rejects.toThrow('MOBILE_HOST_REQUIRED')
      await expect(mobileOnBack(() => undefined)).rejects.toThrow('MOBILE_HOST_REQUIRED')
      await expect(mobileBackground()).rejects.toThrow('MOBILE_HOST_REQUIRED')
      await expect(mobileAppearance('dark', '#0d1117')).rejects.toThrow('MOBILE_HOST_REQUIRED')
      expect(host.back).not.toHaveBeenCalled()
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

  it('forwards the original typed request through the sole native mobile command', async () => {
    host.isTauri.mockReturnValue(true)
    host.invoke.mockResolvedValue({ state: 'prepared', operation_id: 'original' })
    await mobileRequest({ kind: 'observe_save', operation_id: 'original' })
    expect(host.invoke).toHaveBeenCalledExactlyOnceWith('mobile_request', { request: { kind: 'observe_save', operation_id: 'original' } })
  })
  it('keeps native file destinations out of the export request and queries an original delivery by identity',async()=>{
    host.isTauri.mockReturnValue(true);host.invoke.mockResolvedValue({state:'saved'})
    await mobileSaveExport('original-sealed-ticket');await mobileExportDelivery('original-sealed-ticket','original-attempt')
    expect(host.invoke.mock.calls).toEqual([
      ['mobile_save_export',{ticket:'original-sealed-ticket'}],
      ['mobile_export_delivery',{ticket:'original-sealed-ticket',deliveryId:'original-attempt'}],
    ])
  })
  it('uses the Android app listener and the narrow own-task background command',async()=>{
    host.isTauri.mockReturnValue(true);host.invoke.mockResolvedValue({state:'backgrounded'})
    const listener={unregister:vi.fn()},handler=vi.fn();host.back.mockResolvedValue(listener)
    expect(await mobileOnBack(handler)).toBe(listener)
    host.back.mock.calls[0]![0]({canGoBack:false});expect(handler).toHaveBeenCalledTimes(1)
    await mobileBackground();expect(host.invoke).toHaveBeenCalledExactlyOnceWith('mobile_background')
  })
  it('accepts only bounded appearance values and never sends content or a storage path to the platform', async () => {
    host.isTauri.mockReturnValue(true); host.invoke.mockResolvedValue({ state: 'applied' })
    for (const background of ['', 'white', '#fff', '/data/owned', '#ffffff00', 'url(file:///private)']) {
      await expect(mobileAppearance('light', background)).rejects.toThrow('MOBILE_APPEARANCE_INVALID')
    }
    expect(host.invoke).not.toHaveBeenCalled()
    await mobileAppearance('dark', '#0d1117')
    expect(host.invoke).toHaveBeenCalledExactlyOnceWith('mobile_appearance', { theme: 'dark', background: '#0d1117' })
  })
})
