import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import type { ThemeConfig, ThemeManifest, InstalledTheme, ThemePackageInspection } from '@/contracts'
import * as themePkg from '@/services/themePackageService'

const builtinThemes: ThemeConfig[] = [
  { theme_id: 'light', name: '浅色', version: '1.0.0', description: '默认浅色主题', is_dark: false, builtin: true, code_theme: 'github-light' },
  { theme_id: 'dark', name: '深色', version: '1.0.0', description: '默认深色主题', is_dark: true, builtin: true, code_theme: 'github-dark' },
  { theme_id: 'sepia', name: '护眼', version: '1.0.0', description: '护眼暖色调', is_dark: false, builtin: true, code_theme: 'github-light' },
]

export type CodeBlockThemePreference = 'auto' | 'github-light' | 'github-dark'

function isCodeBlockThemePreference(value: unknown): value is CodeBlockThemePreference {
  return value === 'auto' || value === 'github-light' || value === 'github-dark'
}

const builtinToInstalled = (t: ThemeConfig): InstalledTheme => ({
  theme_id: t.theme_id,
  name: t.name,
  version: t.version,
  author: 'NotesAgent 团队',
  description: t.description,
  is_dark: t.is_dark,
  builtin: true,
  enabled: true,
  manifest: {
    theme_id: t.theme_id,
    name: t.name,
    version: t.version,
    author: 'NotesAgent 团队',
    description: t.description,
    min_app_version: '0.1.0',
    is_dark: t.is_dark,
    css_entry: 'builtin',
  },
  code_theme: t.code_theme,
})

export const useThemeStore = defineStore('theme', () => {
  const themes = ref<ThemeConfig[]>([...builtinThemes])
  const installedCustomThemes = ref<InstalledTheme[]>([])
  const currentThemeId = ref<string>('light')
  const fontEditorSize = ref(15)
  const fontEditorFamily = ref('system-ui')
  const lineHeight = ref(1.7)
  const codeBlockTheme = ref<CodeBlockThemePreference>('auto')
  const isImporting = ref(false)
  const importError = ref<string | null>(null)
  const pendingInspection = ref<ThemePackageInspection | null>(null)
  let appearanceHydrated = false

  const allThemes = computed<InstalledTheme[]>(() => [
    ...builtinThemes.map(builtinToInstalled),
    ...installedCustomThemes.value,
  ])

  const currentTheme = computed(() =>
    allThemes.value.find((t) => t.theme_id === currentThemeId.value) || allThemes.value[0]
  )

  const isDark = computed(() => currentTheme.value?.is_dark || false)

  const resolvedCodeBlockTheme = computed<'github-light' | 'github-dark'>(() => {
    if (codeBlockTheme.value !== 'auto') return codeBlockTheme.value
    return currentTheme.value?.code_theme ?? (isDark.value ? 'github-dark' : 'github-light')
  })

  function applyTheme(themeId: string) {
    const theme = allThemes.value.find((t) => t.theme_id === themeId)
    if (!theme) return
    currentThemeId.value = themeId
    const root = document.documentElement
    if (theme.builtin) {
      if (theme.is_dark) {
        root.setAttribute('data-theme', 'dark')
      } else if (themeId === 'sepia') {
        root.setAttribute('data-theme', 'sepia')
      } else {
        root.setAttribute('data-theme', 'light')
      }
    } else {
      root.setAttribute('data-theme', themeId)
    }
    localStorage.setItem('theme', themeId)
  }

  function initTheme() {
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
    void loadCustomThemes()
    const saved = localStorage.getItem('theme')
    appearanceHydrated = true
    persistAppearance()
    if (saved) {
      applyTheme(saved)
      return
    }
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    applyTheme(prefersDark ? 'dark' : 'light')
  }

  async function loadCustomThemes() {
    try {
      const list = await themePkg.listInstalledThemes()
      installedCustomThemes.value = list
      themes.value = [...builtinThemes, ...list.map((t) => ({
        theme_id: t.theme_id,
        name: t.name,
        version: t.version,
        description: t.description ?? '',
        is_dark: t.is_dark,
        builtin: false,
        author: t.author,
        code_theme: t.code_theme,
      }))]
    } catch { /* keep builtin only */ }
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

  async function inspectThemePackage(packageData: string): Promise<ThemePackageInspection> {
    isImporting.value = true
    importError.value = null
    try {
      const result = await themePkg.inspectThemePackage(packageData)
      pendingInspection.value = result
      if (!result.compatible) {
        importError.value = result.warnings[0] ?? '主题包不兼容'
      }
      return result
    } catch (error) {
      importError.value = error instanceof Error ? error.message : '导入失败'
      throw error
    } finally {
      isImporting.value = false
    }
  }

  async function installThemeFromInspection(manifest: ThemeManifest, cssContent: string) {
    isImporting.value = true
    importError.value = null
    try {
      const installed = await themePkg.installTheme(manifest, cssContent)
      const idx = installedCustomThemes.value.findIndex((t) => t.theme_id === installed.theme_id)
      if (idx >= 0) installedCustomThemes.value[idx] = installed
      else installedCustomThemes.value.push(installed)
      const themeConfig: ThemeConfig = {
        theme_id: installed.theme_id,
        name: installed.name,
        version: installed.version,
        description: installed.description ?? '',
        is_dark: installed.is_dark,
        builtin: false,
        author: installed.author,
        code_theme: installed.code_theme,
      }
      const existingIdx = themes.value.findIndex((t) => t.theme_id === installed.theme_id)
      if (existingIdx >= 0) themes.value[existingIdx] = themeConfig
      else themes.value.push(themeConfig)
      pendingInspection.value = null
      return installed
    } catch (error) {
      importError.value = error instanceof Error ? error.message : '安装失败'
      throw error
    } finally {
      isImporting.value = false
    }
  }

  async function uninstallTheme(themeId: string) {
    await themePkg.uninstallTheme(themeId)
    installedCustomThemes.value = installedCustomThemes.value.filter((t) => t.theme_id !== themeId)
    themes.value = themes.value.filter((t) => t.theme_id !== themeId || t.builtin)
    if (currentThemeId.value === themeId) {
      applyTheme('light')
    }
  }

  async function installCommunityTheme(themeId: string) {
    isImporting.value = true
    importError.value = null
    try {
      const installed = await themePkg.installCommunityTheme(themeId)
      const idx = installedCustomThemes.value.findIndex((t) => t.theme_id === installed.theme_id)
      if (idx >= 0) installedCustomThemes.value[idx] = installed
      else installedCustomThemes.value.push(installed)
      const themeConfig: ThemeConfig = {
        theme_id: installed.theme_id,
        name: installed.name,
        version: installed.version,
        description: installed.description ?? '',
        is_dark: installed.is_dark,
        builtin: false,
        author: installed.author,
        code_theme: installed.code_theme,
      }
      const existingIdx = themes.value.findIndex((t) => t.theme_id === installed.theme_id)
      if (existingIdx >= 0) themes.value[existingIdx] = themeConfig
      else themes.value.push(themeConfig)
      return installed
    } catch (error) {
      importError.value = error instanceof Error ? error.message : '安装失败'
      throw error
    } finally {
      isImporting.value = false
    }
  }

  function isThemeInstalled(themeId: string): boolean {
    return themes.value.some((t) => t.theme_id === themeId)
  }

  watch(resolvedCodeBlockTheme, (theme) => {
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
    installedCustomThemes,
    currentThemeId,
    currentTheme,
    isDark,
    fontEditorSize,
    fontEditorFamily,
    lineHeight,
    codeBlockTheme,
    resolvedCodeBlockTheme,
    isImporting,
    importError,
    pendingInspection,
    allThemes,
    applyTheme,
    initTheme,
    toggleTheme,
    resetToDefault,
    loadCustomThemes,
    inspectThemePackage,
    installThemeFromInspection,
    uninstallTheme,
    installCommunityTheme,
    isThemeInstalled,
  }
})
