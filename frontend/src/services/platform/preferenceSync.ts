/** 可移植偏好记录与主用Vault绑定；本地草稿保留自己的 Vault 密钥。 */
import { ref, watch, nextTick } from 'vue'
import { useWorkspaceStore } from '@/stores/workspace'
import { useThemeStore } from '@/stores/theme'
import { useSettingsStore } from '@/stores/settings'
import { useHeadingAppearanceStore, normalizeHeadingAppearance } from '@/stores/headingAppearance'
import { useMarkdownPreferencesStore, normalizeMarkdownPreferences } from '@/stores/markdownPreferences'
import { hostInvoke, isDesktop } from './desktop'
import { RecordBinding } from './recordBinding'
import { useLayoutPreferencesStore } from '@/stores/layoutPreferences'
interface Controller { label: string; binding: Pick<RecordBinding<never>, 'capture' | 'poll' | 'seed' | 'stop' | 'keepLocal' | 'useRemote' | 'error' | 'hasDraft'> }
export const preferenceSyncIssues = ref<Array<{ kind: string; label: string; error: string; hasDraft: boolean }>>([])
let controllers = new Map<string, Controller>(), activeVault = '', installed = false
export async function seedCurrentPreferences(vaultId: string) {
  if (!isDesktop()) return
  if (vaultId !== activeVault || controllers.size !== 3) throw new Error('PREFERENCE_BINDING_NOT_READY')
  for (const { binding } of controllers.values()) {
    await binding.seed()
    if (binding.error) throw new Error(binding.error)
  }
}
export async function resolvePreferenceDraft(kind: string, choice: 'local' | 'remote') {
  const controller = controllers.get(kind)
  if (!controller) return
  if (choice === 'local') { controller.binding.capture(); await controller.binding.keepLocal() }
  else await controller.binding.useRemote()
}
export function installPreferenceSync() {
  if (!isDesktop() || installed) return
  installed = true
  const workspace = useWorkspaceStore(), theme = useThemeStore(), settings = useSettingsStore()
  const headings = useHeadingAppearanceStore(), markdown = useMarkdownPreferencesStore()
  const layout = useLayoutPreferencesStore()
  const readLayout = () => ({ primaryExpanded: layout.primaryExpanded, workspaceWidth: layout.workspaceWidth, chatWidth: layout.chatWidth })
  let applying = 0
  const readTheme = () => ({ themeId: theme.currentThemeId, fontEditorSize: theme.fontEditorSize, fontEditorFamily: theme.fontEditorFamily, lineHeight: theme.lineHeight, codeBlockTheme: theme.codeBlockTheme, headings: normalizeHeadingAppearance(headings.preferences) })
  const readPreferences = () => ({ restoreLastVault: settings.restoreLastVault, autoSaveInterval: settings.autoSaveInterval, language: settings.language, defaultEditorMode: settings.defaultEditorMode, editorLineWidth: settings.editorLineWidth, spellCheck: settings.spellCheck, markdown: normalizeMarkdownPreferences(markdown.preferences), presets: markdown.customPresets.map(preset => ({ name: preset.name, preferences: normalizeMarkdownPreferences(preset.preferences) })) })
  const changed = () => { preferenceSyncIssues.value = [...controllers].filter(([, value]) => value.binding.error).map(([kind, value]) => ({ kind, label: value.label, error: value.binding.error, hasDraft: value.binding.hasDraft })) }
  async function apply(action: () => void) { applying++; try { action(); await nextTick() } finally { applying-- } }
  watch(() => workspace.vaultId, vaultId => {
    for (const value of controllers.values()) value.binding.stop()
    controllers = new Map(); activeVault = vaultId; changed()
    if (!vaultId) return
    const common = { vaultId, invoke: hostInvoke, storage: localStorage, changed }
    controllers.set('theme_settings', { label: '主题与外观', binding: new RecordBinding({ ...common, kind: 'theme_settings', id: 'appearance', read: readTheme, apply: data => apply(() => {
      if (!theme.applyTheme(data.themeId)) { theme.applyTheme('light'); theme.themeLoadWarning = `同步主题 ${data.themeId} 尚未安装，请在本机安装并确认后使用。` }
      theme.fontEditorSize = data.fontEditorSize; theme.fontEditorFamily = data.fontEditorFamily; theme.lineHeight = data.lineHeight; theme.codeBlockTheme = data.codeBlockTheme
      headings.preferences = normalizeHeadingAppearance(data.headings)
    }) }) })
    controllers.set('preferences', { label: '编辑器偏好', binding: new RecordBinding({ ...common, kind: 'preferences', id: 'editor', read: readPreferences, apply: data => apply(() => {
      settings.restoreLastVault = data.restoreLastVault; settings.autoSaveInterval = data.autoSaveInterval; settings.language = data.language
      settings.defaultEditorMode = data.defaultEditorMode; settings.editorLineWidth = data.editorLineWidth; settings.spellCheck = data.spellCheck
      markdown.apply(data.markdown); markdown.customPresets = data.presets.map(preset => ({ name: preset.name, preferences: normalizeMarkdownPreferences(preset.preferences) }))
    }) }) })
    controllers.set('layout', { label: '侧栏布局', binding: new RecordBinding({ ...common, kind: 'layout', id: 'sidebars', read: readLayout, apply: data => apply(() => {
      layout.primaryExpanded = data.primaryExpanded; layout.workspaceWidth = data.workspaceWidth; layout.chatWidth = data.chatWidth
    }) }) })
    changed()
    for (const value of controllers.values()) void value.binding.poll()
  }, { immediate: true, flush: 'sync' })
  watch(readLayout, () => { if (!applying) controllers.get('layout')?.binding.capture() }, { deep: true, flush: 'sync' })
  watch(readTheme, () => { if (!applying) controllers.get('theme_settings')?.binding.capture() }, { deep: true, flush: 'sync' })
  watch(readPreferences, () => { if (!applying) controllers.get('preferences')?.binding.capture() }, { deep: true, flush: 'sync' })
  setInterval(() => { for (const value of controllers.values()) void value.binding.poll() }, 1500)
}
