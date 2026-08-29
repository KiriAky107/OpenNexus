import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import type { ThemeConfig } from '@/contracts'

const builtinThemes: ThemeConfig[] = [
  { theme_id: 'light', name: '浅色', version: '1.0.0', description: '默认浅色主题', is_dark: false, builtin: true },
  { theme_id: 'dark', name: '深色', version: '1.0.0', description: '默认深色主题', is_dark: true, builtin: true },
  { theme_id: 'sepia', name: '护眼', version: '1.0.0', description: '护眼暖色调', is_dark: false, builtin: true },
]

export const useThemeStore = defineStore('theme', () => {
  const themes = ref<ThemeConfig[]>(builtinThemes)
  const currentThemeId = ref<string>('light')
  const fontEditorSize = ref(15)
  const fontEditorFamily = ref('system-ui')
  const lineHeight = ref(1.7)

  const currentTheme = computed(() =>
    themes.value.find((t) => t.theme_id === currentThemeId.value) || themes.value[0]
  )

  const isDark = computed(() => currentTheme.value?.is_dark || false)

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
    const saved = localStorage.getItem('theme')
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
  }

  watch(fontEditorSize, (v) => {
    document.documentElement.style.setProperty('--font-editor-size', `${v}px`)
  })

  watch(lineHeight, (v) => {
    document.documentElement.style.setProperty('--font-editor-line-height', String(v))
  })

  return {
    themes,
    currentThemeId,
    currentTheme,
    isDark,
    fontEditorSize,
    fontEditorFamily,
    lineHeight,
    applyTheme,
    initTheme,
    toggleTheme,
    resetToDefault,
  }
})
