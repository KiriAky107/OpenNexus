// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ThemesView from './ThemesView.vue'
import { useThemeStore } from '@/stores/theme'
import { mockCommunityThemes, getCommunityThemePreviewCss } from '@/services/themePackageService'

let wrapper: VueWrapper
beforeEach(() => { localStorage.clear(); setActivePinia(createPinia()) })
afterEach(() => { wrapper?.unmount(); vi.useRealTimers() })

it.each(mockCommunityThemes)('previews uninstalled $theme_id using its actual CSS without changing the active theme', async theme => {
  const store = useThemeStore()
  store.applyTheme('light')
  wrapper = mount(ThemesView, { global: { stubs: { MarkdownContent: true } } })
  await flushPromises()
  await wrapper.findAll('.tab-btn')[1]!.trigger('click')
  const card = wrapper.findAll('article.theme-card').find(item => item.text().includes(theme.name))!
  await card.findAll('button').find(button => button.text() === '预览')!.trigger('click')
  expect(wrapper.get('[role="dialog"]').text()).toContain(theme.name)
  const frame = wrapper.get('iframe')
  expect(frame.attributes('sandbox')).toBe('')
  const preview = new DOMParser().parseFromString(frame.attributes('srcdoc')!, 'text/html')
  expect(preview.documentElement.dataset.theme).toBe(theme.theme_id)
  expect(preview.querySelector('style')!.textContent).toContain(getCommunityThemePreviewCss(theme.theme_id))
  expect(store.currentThemeId).toBe('light')
  expect(localStorage.getItem('theme')).toBe('light')
  expect(store.isThemeInstalled(theme.theme_id)).toBe(false)
  expect(document.head.querySelectorAll('style[id^="theme-style-"]')).toHaveLength(0)
  vi.useFakeTimers()
  await wrapper.get('[role="dialog"] button').trigger('click')
  store.applyTheme('dark')
  await vi.advanceTimersByTimeAsync(2000)
  expect(wrapper.find('iframe').exists()).toBe(false)
  expect(store.currentThemeId).toBe('dark')
})
