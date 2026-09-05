// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import type { InstalledTheme } from '@/contracts'
import { useThemeStore } from './theme'
import * as themePkg from '@/services/themePackageService'

vi.mock('@/services/themePackageService', () => ({
  listInstalledThemes: vi.fn(async () => []),
  inspectThemePackage: vi.fn(),
  installTheme: vi.fn(),
  uninstallTheme: vi.fn(),
  installCommunityTheme: vi.fn(),
  setActiveCustomTheme: vi.fn(),
}))

const listInstalledThemes = vi.mocked(themePkg.listInstalledThemes)

function customTheme(themeId: string, isDark = false): InstalledTheme {
  return {
    theme_id: themeId,
    name: themeId,
    version: '1.0.0',
    author: '社区',
    is_dark: isDark,
    builtin: false,
    enabled: true,
    manifest: {
      theme_id: themeId,
      name: themeId,
      version: '1.0.0',
      author: '社区',
      min_app_version: '0.1.0',
      is_dark: isDark,
      css_entry: 'theme.css',
    },
  }
}

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  document.documentElement.removeAttribute('data-code-theme')
  setActivePinia(createPinia())
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: () => ({ matches: false }),
  })
  listInstalledThemes.mockReset()
  listInstalledThemes.mockResolvedValue([])
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
    await store.initTheme()
    await nextTick()

    expect(store.codeBlockTheme).toBe('github-dark')
    expect(document.documentElement.dataset.codeTheme).toBe('github-dark')
  })
})

describe('initTheme 恢复已保存主题', () => {
  it('等自定义主题加载完成后再恢复，不会停在没有 data-theme 的裸状态', async () => {
    // 回归：之前这里是 `void loadCustomThemes()` 没有 await，
    // applyTheme('ocean') 在主题列表到达前找不到主题直接 return，
    // 页面上一个 data-theme 都没有。
    localStorage.setItem('theme', 'ocean')
    listInstalledThemes.mockResolvedValue([customTheme('ocean', true)])

    const store = useThemeStore()
    await store.initTheme()

    expect(document.documentElement.getAttribute('data-theme')).toBe('ocean')
    expect(store.currentThemeId).toBe('ocean')
    expect(store.themeLoadWarning).toBeNull()
  })

  it('首屏先同步落内置主题兜底，且不覆盖保存的自定义主题 id', async () => {
    localStorage.setItem('theme', 'ocean')
    let resolveList: (themes: InstalledTheme[]) => void = () => {}
    listInstalledThemes.mockReturnValue(
      new Promise<InstalledTheme[]>((resolve) => { resolveList = resolve }),
    )

    const store = useThemeStore()
    const pending = store.initTheme()

    // 接口还没回来：页面已经有兜底主题，不是裸的
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    // 兜底不能把用户存的主题 id 冲掉，否则刷新后自定义主题就丢了
    expect(localStorage.getItem('theme')).toBe('ocean')

    resolveList([customTheme('ocean', true)])
    await pending

    expect(document.documentElement.getAttribute('data-theme')).toBe('ocean')
  })

  it('保存的主题已被卸载时回退到默认主题并给出提示', async () => {
    localStorage.setItem('theme', 'removed-theme')
    listInstalledThemes.mockResolvedValue([])

    const store = useThemeStore()
    await store.initTheme()

    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(store.currentThemeId).toBe('light')
    expect(store.themeLoadWarning).toContain('removed-theme')
    // 失效记录要清掉，避免每次启动都报一遍
    expect(localStorage.getItem('theme')).toBe('light')
  })

  it('主题列表加载失败时提示用户，而不是静默只剩内置主题', async () => {
    localStorage.setItem('theme', 'dark')
    listInstalledThemes.mockRejectedValue(new Error('网络不可用'))

    const store = useThemeStore()
    await store.initTheme()

    expect(store.themeLoadWarning).toBe('自定义主题加载失败：网络不可用')
    // 内置主题仍然要正常恢复
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })

  it('没有保存过主题时按系统偏好选择', async () => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: () => ({ matches: true }),
    })

    const store = useThemeStore()
    await store.initTheme()

    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(localStorage.getItem('theme')).toBe('dark')
  })
})

describe('applyTheme 返回值', () => {
  it('主题不存在时返回 false 且不改动 data-theme', () => {
    const store = useThemeStore()
    store.applyTheme('light')

    expect(store.applyTheme('not-installed')).toBe(false)
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(store.currentThemeId).toBe('light')
  })

  it('persist: false 时不写 localStorage', () => {
    const store = useThemeStore()
    expect(store.applyTheme('sepia', { persist: false })).toBe(true)

    expect(document.documentElement.getAttribute('data-theme')).toBe('sepia')
    expect(localStorage.getItem('theme')).toBeNull()
  })
})
