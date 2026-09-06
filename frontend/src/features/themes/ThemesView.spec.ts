// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ThemesView from './ThemesView.vue'
import { useThemeStore } from '@/stores/theme'
import { mockCommunityThemes, getCommunityThemePreviewCss, inspectThemePackage } from '@/services/themePackageService'
import paperPackage from '@/assets/themes/paper-moments.theme?raw'

let wrapper: VueWrapper
beforeEach(() => { localStorage.clear(); setActivePinia(createPinia()) })
afterEach(() => { useThemeStore().applyTheme('light'); wrapper?.unmount(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

it('downloads a URL for inspection without automatically installing it', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(paperPackage)))
  wrapper = mount(ThemesView, { global: { stubs: { MarkdownContent: true } } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '导入主题')!.trigger('click')
  await wrapper.get('#theme-package-url').setValue('https://example.com/paper.theme')
  await wrapper.get('.url-import').trigger('submit')
  await vi.waitFor(() => expect(useThemeStore().pendingInspection?.compatible).toBe(true))
  expect(useThemeStore().isThemeInstalled('paper-moments')).toBe(false)
  expect(wrapper.get('.inspection-result').text()).toContain('纸间时光')
  await wrapper.findAll('button').find(button => button.text() === '预览主题效果')!.trigger('click')
  const preview = wrapper.get('iframe')
  expect(preview.attributes('sandbox')).toBe('')
  expect(preview.attributes('srcdoc')).toContain('Content-Security-Policy')
  expect(preview.attributes('srcdoc')).toContain('data-theme="paper-moments"')
  expect(useThemeStore().isThemeInstalled('paper-moments')).toBe(false)
})

it('ignores a URL response after the dialog is cancelled', async () => {
  let respond!: (response: Response) => void
  vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => { respond = resolve })))
  wrapper = mount(ThemesView, { global: { stubs: { MarkdownContent: true } } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '导入主题')!.trigger('click')
  await wrapper.get('#theme-package-url').setValue('https://example.com/paper.theme')
  await wrapper.get('.url-import').trigger('submit')
  await wrapper.get('.import-modal .inline-actions button').trigger('click')
  respond(new Response(paperPackage))
  await flushPromises()
  expect(useThemeStore().pendingInspection).toBeNull()
  expect(wrapper.find('.import-modal').exists()).toBe(false)
})

it('opens the file picker from the styled button and imports the actual paper theme', async () => {
  const store = useThemeStore()
  wrapper = mount(ThemesView, { global: { stubs: { MarkdownContent: true } } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '导入主题')!.trigger('click')
  const input = wrapper.get<HTMLInputElement>('input[type="file"]')
  const click = vi.spyOn(input.element, 'click').mockImplementation(() => {})
  await wrapper.get('.upload-area .button-primary').trigger('click')
  expect(click).toHaveBeenCalledOnce()
  Object.defineProperty(input.element, 'files', { value: [new File([paperPackage], 'paper-moments.theme', { type: 'text/plain' })] })
  await input.trigger('change')
  await vi.waitFor(() => expect(store.pendingInspection?.compatible).toBe(true))
  expect(store.pendingInspection!.warnings).toEqual([])
  await wrapper.get('.import-modal .inline-actions .button-primary').trigger('click')
  await flushPromises()
  expect(store.isThemeInstalled('paper-moments')).toBe(true)
  expect(localStorage.getItem('installed-themes-css-paper-moments')).toBe(getCommunityThemePreviewCss('paper-moments'))
  expect(document.getElementById('theme-style-paper-moments')).toBeNull()
  store.applyTheme('paper-moments')
  expect(document.getElementById('theme-style-paper-moments')!.textContent).toBe(getCommunityThemePreviewCss('paper-moments'))
  store.applyTheme('light')
  expect(document.getElementById('theme-style-paper-moments')).toBeNull()
})

it.each(mockCommunityThemes)('previews uninstalled $theme_id using its actual CSS without changing the active theme', async theme => {
  const store = useThemeStore()
  store.applyTheme('light')
  wrapper = mount(ThemesView, { global: { stubs: { MarkdownContent: true } } })
  await flushPromises()
  await wrapper.findAll('.tab-btn')[1]!.trigger('click')
  const card = wrapper.findAll('article.theme-card').find(item => item.text().includes(theme.name))!
  await card.findAll('button').find(button => button.text() === '预览')!.trigger('click')
  expect(wrapper.get('dialog.app-dialog').text()).toContain(theme.name)
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
  await wrapper.get('dialog.app-dialog button').trigger('click')
  store.applyTheme('dark')
  await vi.advanceTimersByTimeAsync(2000)
  expect(wrapper.find('iframe').exists()).toBe(false)
  expect(store.currentThemeId).toBe('dark')
})

it('offers and applies the paper theme update without discarding the active theme', async () => {
  const store = useThemeStore()
  const old = await inspectThemePackage(paperPackage.replace('version: 1.8.0', 'version: 1.6.1'))
  await store.installThemeFromInspection(old.manifest, old.css)
  store.applyTheme('paper-moments')
  wrapper = mount(ThemesView, { global: { stubs: { MarkdownContent: true } } })
  await flushPromises()
  await wrapper.findAll('.tab-btn')[1]!.trigger('click')
  const card = wrapper.findAll('article.theme-card').find(item => item.text().includes('Paper Moments'))!
  await card.findAll('button').find(button => button.text() === '更新')!.trigger('click')
  await flushPromises()
  expect(store.allThemes.find(theme => theme.theme_id === 'paper-moments')?.version).toBe('1.8.0')
  expect(document.getElementById('theme-style-paper-moments')!.textContent).toContain('.surface-nested')
})
