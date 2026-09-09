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
  watch(() => [primaryExpanded.value, workspaceWidth.value, chatWidth.value], () => {
    try {
      localStorage.setItem('primary-sidebar-expanded', String(primaryExpanded.value))
      localStorage.setItem('workspace-sidebar-width', String(workspaceWidth.value))
      localStorage.setItem('chat-sidebar-width', String(chatWidth.value))
    } catch { /* 当本地存储不可用时，保持当前布局可用。 */ }
  }, { flush: 'sync' })
  return { primaryExpanded, workspaceWidth, chatWidth }
})
