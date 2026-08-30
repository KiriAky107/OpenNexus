import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import type { ThemeConfig } from '@/contracts'

const builtinThemes: ThemeConfig[] = [
  { theme_id: 'light', name: '浅色', version: '1.0.0', description: '默认浅色主题', is_dark: false, builtin: true, code_theme: 'github-light' },
  { theme_id: 'dark', name: '深色', version: '1.0.0', description: '默认深色主题', is_dark: true, builtin: true, code_theme: 'github-dark' },
  { theme_id: 'sepia', name: '护眼', version: '1.0.0', description: '护眼暖色调', is_dark: false, builtin: true, code_theme: 'github-light' },
]

export type CodeBlockThemePreference = 'auto' | 'github-light' | 'github-dark'

function isCodeBlockThemePreference(value: unknown): value is CodeBlockThemePreference {
  return value === 'auto' || value === 'github-light' || value === 'github-dark'
}

export const useThemeStore = defineStore('theme', () => {
  const themes = ref<ThemeConfig[]>(builtinThemes)
  const currentThemeId = ref<string>('light')
  const fontEditorSize = ref(15)
  const fontEditorFamily = ref('system-ui')
  const lineHeight = ref(1.7)
  const codeBlockTheme = ref<CodeBlockThemePreference>('auto')
  let appearanceHydrated = false

  const currentTheme = computed(() =>
    themes.value.find((t) => t.theme_id === currentThemeId.value) || themes.value[0]
  )

  const isDark = computed(() => currentTheme.value?.is_dark || false)
  const resolvedCodeBlockTheme = computed<'github-light' | 'github-dark'>(() => {
    if (codeBlockTheme.value !== 'auto') return codeBlockTheme.value
    return currentTheme.value?.code_theme ?? (isDark.value ? 'github-dark' : 'github-light')
  })

  function applyTheme(themeId: string) {
    const theme = themes.value.find((t) => t.theme_id === themeId)
    if (!theme) return
    currentThemeId.value = themeId
    const root = document.documentElement
    if (theme.is_dark) {
      root.setAttribute('data-theme', 'dark')
    } else if (themeId === 'sepia') {
      root.setAttribute('data-theme', 'sepia')
    } else {
      root.setAttribute('data-theme', 'light')
    }
    localStorage.setItem('theme', themeId)
  }

  function initTheme() {
    // 先恢复外观再开放 watch 持久化，避免 immediate watcher 覆盖本地设置。
    const savedAppearance = localStorage.getItem('editor-appearance')
    if (savedAppearance) {
      try {
        const value = JSON.parse(savedAppearance) as { size?: number; family?: string; lineHeight?: number; codeBlockTheme?: unknown }
        if (value.size) fontEditorSize.value = value.size
        if (value.family) fontEditorFamily.value = value.family
        if (value.lineHeight) lineHeight.value = value.lineHeight
        if (isCodeBlockThemePreference(value.codeBlockTheme)) codeBlockTheme.value = value.codeBlockTheme
      } catch { localStorage.removeItem('editor-appearance') }
    }
    const saved = localStorage.getItem('theme')
    appearanceHydrated = true
    persistAppearance()
    if (saved && themes.value.find((t) => t.theme_id === saved)) {
      applyTheme(saved)
      return
    }
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    applyTheme(prefersDark ? 'dark' : 'light')
  }

  function toggleTheme() {
    applyTheme(isDark.value ? 'light' : 'dark')
  }

  function resetToDefault() {
    applyTheme('light')
    fontEditorSize.value = 15
    fontEditorFamily.value = 'system-ui'
    lineHeight.value = 1.7
    codeBlockTheme.value = 'auto'
  }

  const persistAppearance = () => localStorage.setItem('editor-appearance', JSON.stringify({
    size: fontEditorSize.value,
    family: fontEditorFamily.value,
    lineHeight: lineHeight.value,
    codeBlockTheme: codeBlockTheme.value,
  }))

  watch(resolvedCodeBlockTheme, (theme) => {
    // CSS 与 Shiki 共用该属性，确保代码块背景和 token 配色始终成套切换。
    document.documentElement.setAttribute('data-code-theme', theme)
  }, { immediate: true })

  watch(fontEditorSize, (v) => {
    document.documentElement.style.setProperty('--font-editor-size', `${v}px`)
    if (appearanceHydrated) persistAppearance()
  }, { immediate: true })

  watch(lineHeight, (v) => {
    document.documentElement.style.setProperty('--font-editor-line-height', String(v))
    if (appearanceHydrated) persistAppearance()
  }, { immediate: true })

  watch(fontEditorFamily, (v) => {
    document.documentElement.style.setProperty('--font-editor-sans', v)
    if (appearanceHydrated) persistAppearance()
  }, { immediate: true })

  watch(codeBlockTheme, () => {
    if (appearanceHydrated) persistAppearance()
  })

  return {
    themes,
    currentThemeId,
    currentTheme,
    isDark,
    fontEditorSize,
    fontEditorFamily,
    lineHeight,
    codeBlockTheme,
    resolvedCodeBlockTheme,
    applyTheme,
    initTheme,
    toggleTheme,
    resetToDefault,
  }
})
