import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

export const useLayoutPreferencesStore = defineStore('layoutPreferences', () => {
  function stored(key: string) { try { return localStorage.getItem(key) } catch { return null } }
  function width(key: string) {
    const value = Number(stored(key))
    return Number.isFinite(value) && value >= 200 ? Math.min(520, value) : 272
  }
  const primaryExpanded = ref(stored('primary-sidebar-expanded') === 'true')
  const workspaceWidth = ref(width('workspace-sidebar-width'))
  const chatWidth = ref(width('chat-sidebar-width'))
  // Local presentation choices; do not add them to the portable sync schema.
  const editorToolbarVisible = ref(stored('editor-toolbar-visible') !== 'false')
  const canvasInspectorVisible = ref(stored('canvas-inspector-visible') === 'true')
  watch(canvasInspectorVisible, value => {
    try { localStorage.setItem('canvas-inspector-visible', String(value)) } catch { /* Session-only preference. */ }
  }, { flush: 'sync' })
  const workspaceCollapsed = ref(stored('workspace-sidebar-collapsed') === 'true')
  const workspaceTab = ref<'files' | 'outline'>(stored('workspace-sidebar-tab') === 'outline' ? 'outline' : 'files')
  const chatSidebarCollapsed = ref(stored('chat-sidebar-collapsed') === 'true')
  const chatSettingsCollapsed = ref(stored('chat-settings-collapsed') === 'true')
  // Focus is temporary; saved panel choices are restored simply by leaving it.
  const focusMode = ref(false)
  function toggleFocusMode() { focusMode.value = !focusMode.value }
  function toggleChatSidebar() {
    chatSidebarCollapsed.value = !chatSidebarCollapsed.value
  }
  function toggleChatSettings() {
    chatSettingsCollapsed.value = !chatSettingsCollapsed.value
  }
  function showWorkspacePanel(tab: 'files' | 'outline') {
    workspaceTab.value = tab
    workspaceCollapsed.value = false
  }
  function toggleWorkspacePanel(tab: 'files' | 'outline') {
    if (!workspaceCollapsed.value && workspaceTab.value === tab) workspaceCollapsed.value = true
    else showWorkspacePanel(tab)
  }
  watch(() => [editorToolbarVisible.value, workspaceCollapsed.value, workspaceTab.value], () => {
    try {
      localStorage.setItem('editor-toolbar-visible', String(editorToolbarVisible.value))
      localStorage.setItem('workspace-sidebar-collapsed', String(workspaceCollapsed.value))
      localStorage.setItem('workspace-sidebar-tab', workspaceTab.value)
    } catch { /* Keep the current session usable without local storage. */ }
  }, { flush: 'sync' })
  watch(() => [primaryExpanded.value, workspaceWidth.value, chatWidth.value], () => {
    try {
      localStorage.setItem('primary-sidebar-expanded', String(primaryExpanded.value))
      localStorage.setItem('workspace-sidebar-width', String(workspaceWidth.value))
      localStorage.setItem('chat-sidebar-width', String(chatWidth.value))
    } catch { /* 当本地存储不可用时，保持当前布局可用。 */ }
  }, { flush: 'sync' })
  watch(() => [chatSidebarCollapsed.value, chatSettingsCollapsed.value], () => {
    try {
      localStorage.setItem('chat-sidebar-collapsed', String(chatSidebarCollapsed.value))
      localStorage.setItem('chat-settings-collapsed', String(chatSettingsCollapsed.value))
    } catch { /* Keep the current session usable without local storage. */ }
  }, { flush: 'sync' })
  return { primaryExpanded, workspaceWidth, chatWidth, editorToolbarVisible, canvasInspectorVisible, workspaceCollapsed, workspaceTab,
    chatSidebarCollapsed, chatSettingsCollapsed, focusMode, toggleFocusMode,
    toggleChatSidebar, toggleChatSettings, showWorkspacePanel, toggleWorkspacePanel }
})
