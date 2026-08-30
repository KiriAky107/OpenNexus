// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { useThemeStore } from './theme'

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  document.documentElement.removeAttribute('data-code-theme')
  setActivePinia(createPinia())
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: () => ({ matches: false }),
  })
})

describe('代码块主题偏好', () => {
  it('跟随应用主题选择对应的 GitHub 代码主题', async () => {
    const store = useThemeStore()
    store.applyTheme('dark')
    await nextTick()

    expect(store.resolvedCodeBlockTheme).toBe('github-dark')
    expect(document.documentElement.dataset.codeTheme).toBe('github-dark')
  })

  it('允许代码块主题独立于应用主题', async () => {
    const store = useThemeStore()
    store.applyTheme('dark')
    store.codeBlockTheme = 'github-light'
    await nextTick()

    expect(store.resolvedCodeBlockTheme).toBe('github-light')
    expect(document.documentElement.dataset.codeTheme).toBe('github-light')
  })

  it('恢复持久化的代码块主题偏好', async () => {
    localStorage.setItem('editor-appearance', JSON.stringify({ codeBlockTheme: 'github-dark' }))
    const store = useThemeStore()
    store.initTheme()
    await nextTick()

    expect(store.codeBlockTheme).toBe('github-dark')
    expect(document.documentElement.dataset.codeTheme).toBe('github-dark')
  })
})
