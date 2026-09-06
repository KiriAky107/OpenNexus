import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import type { ThemeConfig, ThemeManifest, InstalledTheme, ThemePackageInspection } from '@/contracts'
import * as themePkg from '@/services/themePackageService'
import { t } from '@/i18n'

const builtinThemes = (): ThemeConfig[] => [
  { theme_id: 'light', name: t('浅色', 'Light'), version: '1.2.0', description: t('默认浅色主题', 'Default light theme'), is_dark: false, builtin: true, code_theme: 'github-light' },
  { theme_id: 'dark', name: t('深色', 'Dark'), version: '1.2.0', description: t('默认深色主题', 'Default dark theme'), is_dark: true, builtin: true, code_theme: 'github-dark' },
  { theme_id: 'sepia', name: t('护眼', 'Sepia'), version: '1.2.0', description: t('护眼暖色调', 'Warm, low-glare theme'), is_dark: false, builtin: true, code_theme: 'github-light' },
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
  const themes = computed<ThemeConfig[]>(() => [...builtinThemes(), ...installedCustomThemes.value.map(theme => ({ ...theme, description: theme.description ?? '' }))])
  const installedCustomThemes = ref<InstalledTheme[]>([])
  const currentThemeId = ref<string>('light')
  const fontEditorSize = ref(15)
  const fontEditorFamily = ref('system-ui')
  const lineHeight = ref(1.7)
  const codeBlockTheme = ref<CodeBlockThemePreference>('auto')
  const isImporting = ref(false)
  const importError = ref<string | null>(null)
  // 主题恢复阶段的提示（保存的主题已卸载、主题列表加载失败等），与导入错误分开。
  const themeLoadWarning = ref<string | null>(null)
  const pendingInspection = ref<ThemePackageInspection | null>(null)
  let appearanceHydrated = false

  const allThemes = computed<InstalledTheme[]>(() => [
    ...builtinThemes().map(builtinToInstalled),
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

  /** 应用主题；返回 false 表示该主题当前不存在（未安装或还没加载完）。 */
  function applyTheme(themeId: string, options: { persist?: boolean } = {}): boolean {
    const theme = allThemes.value.find((t) => t.theme_id === themeId)
    if (!theme) return false
    themePkg.setActiveCustomTheme(theme.builtin ? null : themeId)
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
    if (options.persist !== false) localStorage.setItem('theme', themeId)
    return true
  }

  function isBuiltinThemeId(themeId: string): boolean {
    return builtinThemes().some((t) => t.theme_id === themeId)
  }

  function systemThemeId(): string {
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }

  /**
   * 恢复外观与主题。
   *
   * 自定义主题要等 listInstalledThemes 回来才存在于 allThemes 里，
   * 所以恢复已保存主题必须在 loadCustomThemes 之后 —— 否则 applyTheme
   * 找不到主题直接 return，页面会停在没有 data-theme 的裸状态。
   * 首屏也不能干等接口：先同步落一个内置主题兜底（不写 localStorage，
   * 以免把用户存的自定义主题 id 冲掉），加载完成后再切到真正保存的那个。
   */
  async function initTheme(): Promise<void> {
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
    appearanceHydrated = true
    persistAppearance()

    const saved = localStorage.getItem('theme')
    const fallback = systemThemeId()
    applyTheme(saved && isBuiltinThemeId(saved) ? saved : fallback, { persist: false })

    await loadCustomThemes()

    if (!saved) {
      applyTheme(fallback)
      return
    }
    if (applyTheme(saved)) return

    // 保存的主题已被卸载，或主题列表加载失败：回退并清掉失效记录。
    themeLoadWarning.value = `主题「${saved}」已不可用，已回退到默认主题。`
    localStorage.removeItem('theme')
    applyTheme(fallback)
  }

  async function loadCustomThemes() {
    try {
      const list = await themePkg.listInstalledThemes()
      installedCustomThemes.value = list
      themeLoadWarning.value = null
    } catch (error) {
      // 只保留内置主题，但要让用户知道自定义主题这次没加载上。
      themeLoadWarning.value = error instanceof Error
        ? `自定义主题加载失败：${error.message}`
        : '自定义主题加载失败。'
    }
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
      if (currentThemeId.value === installed.theme_id) applyTheme(installed.theme_id)
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
      if (currentThemeId.value === installed.theme_id) applyTheme(installed.theme_id)
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
    themeLoadWarning,
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
