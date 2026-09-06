// @vitest-environment happy-dom
import { beforeEach, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { useMarkdownPreferencesStore, markdownPresets } from './markdownPreferences'
beforeEach(() => { localStorage.clear(); setActivePinia(createPinia()) })
it('saves, replaces and restores named syntax presets', async () => {
  const store = useMarkdownPreferencesStore()
  store.preferences.heading = 'setext'
  store.preferences.bullet = '+'
  expect(store.savePreset('我的格式')).toBe(true)
  store.apply(markdownPresets.plain)
  expect(store.normalized.callouts).toBe(false)
  store.apply(store.customPresets[0]!.preferences)
  expect(store.normalized.heading).toBe('setext')
  await nextTick()
  setActivePinia(createPinia())
  expect(useMarkdownPreferencesStore().normalized.bullet).toBe('+')
  expect(useMarkdownPreferencesStore().customPresets[0]!.name).toBe('我的格式')
})
