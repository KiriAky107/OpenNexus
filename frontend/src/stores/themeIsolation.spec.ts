// @vitest-environment happy-dom
import { beforeEach, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useThemeStore } from './theme'
import { installTheme } from '@/services/themePackageService'
import type { ThemeManifest } from '@/contracts'

const manifest = (id: string): ThemeManifest => ({ theme_id: id, name: id, version: '1.0.0', author: 'test', min_app_version: '0.1.0', is_dark: false, css_entry: 'theme.css' })
beforeEach(() => {
  localStorage.clear()
  document.head.querySelectorAll('style[id^="theme-style-"]').forEach(el => el.remove())
  setActivePinia(createPinia())
})

it('does not apply installed CSS until selected and removes it when returning to a builtin theme', async () => {
  const store = useThemeStore()
  store.applyTheme('light')
  const initialColor = getComputedStyle(document.body).color
  await store.installThemeFromInspection(manifest('first'), 'body { color: rgb(1, 2, 3) !important; }')
  await store.loadCustomThemes()
  expect(getComputedStyle(document.body).color).toBe(initialColor)
  expect(document.head.querySelectorAll('style[id^="theme-style-"]')).toHaveLength(0)
  store.applyTheme('first')
  expect(getComputedStyle(document.body).color).toBe('rgb(1, 2, 3)')
  store.applyTheme('dark')
  expect(getComputedStyle(document.body).color).toBe(initialColor)
  expect(document.head.querySelectorAll('style[id^="theme-style-"]')).toHaveLength(0)
})

it('keeps only the selected custom theme mounted, including after a list reload', async () => {
  const store = useThemeStore()
  await installTheme(manifest('first'), 'body { color: rgb(1, 2, 3) !important; }')
  await installTheme(manifest('second'), 'body { background-color: rgb(4, 5, 6) !important; }')
  await store.loadCustomThemes()
  store.applyTheme('first')
  await store.loadCustomThemes()
  expect(document.head.querySelectorAll('style[id^="theme-style-"]')).toHaveLength(1)
  store.applyTheme('second')
  expect(document.getElementById('theme-style-first')).toBeNull()
  expect(document.getElementById('theme-style-second')).not.toBeNull()
  await store.uninstallTheme('second')
  expect(store.currentThemeId).toBe('light')
  expect(document.head.querySelectorAll('style[id^="theme-style-"]')).toHaveLength(0)
})

it('restores only the saved custom theme on startup', async () => {
  await installTheme(manifest('first'), 'body { color: rgb(1, 2, 3); }')
  await installTheme(manifest('second'), 'body { color: rgb(4, 5, 6); }')
  localStorage.setItem('theme', 'first')
  const store = useThemeStore()
  await store.initTheme()
  expect(store.currentThemeId).toBe('first')
  expect(document.getElementById('theme-style-first')).not.toBeNull()
  expect(document.getElementById('theme-style-second')).toBeNull()
})
