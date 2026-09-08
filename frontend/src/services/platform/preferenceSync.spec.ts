// @vitest-environment happy-dom
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import { useWorkspaceStore } from '@/stores/workspace'
import { useThemeStore } from '@/stores/theme'
import { useSettingsStore } from '@/stores/settings'
import { hostInvoke } from './desktop'
import { installPreferenceSync, seedCurrentPreferences } from './preferenceSync'
vi.mock('./desktop', () => ({ isDesktop: () => true, hostInvoke: vi.fn(), nativePath: (value: string) => value, nativeTree: () => [] }))
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); localStorage.clear() })
it('applies remote appearance without echoing it and only exports approved local fields', async () => {
  vi.useFakeTimers(); setActivePinia(createPinia())
  const workspace = useWorkspaceStore(), theme = useThemeStore(), settings = useSettingsStore()
  settings.permissionPolicy = { plantedSecretPermission: 'allow' }
  const data = { themeId: 'dark', fontEditorSize: 18, fontEditorFamily: 'system-ui', lineHeight: 1.7, codeBlockTheme: 'auto', headings: { custom: false, family: 'inherit', levels: [32,28,24,21,18,16].map(size => ({ size, weight: 700 })) } }
  let remote: Record<string, unknown> = { record: { schema: 1, kind: 'theme_settings', id: 'appearance', data }, hash: '1'.repeat(64), file_id: 'theme-file' }
  vi.mocked(hostInvoke).mockImplementation(async (command, args) => {
    const request = args!.request as { kind: string; record?: Record<string, unknown> }
    if (command === 'record_get') return request.kind === 'theme_settings' ? remote : null
    const result = { record: request.record, hash: '2'.repeat(64), file_id: 'theme-file' }
    if (request.record?.kind === 'theme_settings') remote = result
    return result
  })
  workspace.vaultId = 'one'; installPreferenceSync(); await flushPromises()
  expect(theme.fontEditorSize).toBe(18); expect(theme.currentThemeId).toBe('dark')
  expect(vi.mocked(hostInvoke).mock.calls.filter(([command]) => command === 'record_write')).toHaveLength(0)
  theme.fontEditorSize = 24; await vi.advanceTimersByTimeAsync(1500); await flushPromises()
  let writes = vi.mocked(hostInvoke).mock.calls.filter(([command]) => command === 'record_write')
  expect(writes).toHaveLength(1)
  expect(JSON.stringify(writes)).not.toContain('plantedSecretPermission')
  await seedCurrentPreferences('one')
  writes = vi.mocked(hostInvoke).mock.calls.filter(([command]) => command === 'record_write')
  expect(writes.some(([, args]) => (args!.request as { record: { kind: string } }).record.kind === 'preferences')).toBe(true)
  expect(JSON.stringify(writes)).not.toContain('permissionPolicy')
  await expect(seedCurrentPreferences('other')).rejects.toThrow('PREFERENCE_BINDING_NOT_READY')
})
