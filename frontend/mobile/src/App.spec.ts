// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import App from './App.vue'

const host = vi.hoisted(() => ({ mobileCapabilities: vi.fn(), mobileVault: vi.fn(), mobileRequest: vi.fn() }))
vi.mock('./native', () => host)

beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
  localStorage.setItem('opennexus.mobile.language', 'en')
  host.mobileCapabilities.mockResolvedValue({ protocol: 1, platform: 'android', workspace: true, notes: true, version: '0.1.0-dev1' })
  host.mobileVault.mockResolvedValue({ vault_id: 'owned-vault', note_count: 0 })
  host.mobileRequest.mockResolvedValue({ items: [], total: 0, directories: [], unsearched_large_notes: 0 })
})

it('changes interface language without rereading or changing the native vault', async () => {
  const wrapper = mount(App)
  await flushPromises()
  expect(wrapper.text()).toContain('Local vault opened')
  const nativeReads = host.mobileRequest.mock.calls.length
  await wrapper.get('.workspace-header select').setValue('zh-CN')
  await flushPromises()
  expect(wrapper.text()).toContain('本机知识库已打开')
  expect(document.documentElement.lang).toBe('zh-CN')
  expect(localStorage.getItem('opennexus.mobile.language')).toBe('zh-CN')
  expect(host.mobileCapabilities).toHaveBeenCalledTimes(1)
  expect(host.mobileVault).toHaveBeenCalledTimes(1)
  expect(host.mobileRequest).toHaveBeenCalledTimes(nativeReads)
  wrapper.unmount()
})
