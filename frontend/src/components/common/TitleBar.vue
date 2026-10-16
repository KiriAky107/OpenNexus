<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useWorkspaceStore } from '@/stores/workspace'
import { useEditorStore } from '@/stores/editor'
import { useThemeStore } from '@/stores/theme'
import { useLayoutPreferencesStore } from '@/stores/layoutPreferences'
import ControlIcon from './ControlIcon.vue'
import TitleBarFrame from './TitleBarFrame.vue'
import { t } from '@/i18n'
import { isDesktop } from '@/services/platform/desktop'
import { minimizeWindow, observeWindowMaximized, requestWindowClose, toggleMaximizeWindow } from '@/services/platform/windowControls'

const route = useRoute()
const workspaceStore = useWorkspaceStore()
const editorStore = useEditorStore()
const themeStore = useThemeStore()
const layout = useLayoutPreferencesStore()
const desktop = isDesktop()
const maximized = ref(false)
let disposed = false
let stopWindowObserver: (() => void) | undefined
onMounted(async () => {
  try {
    const stop = await observeWindowMaximized(value => { maximized.value = value })
    if (disposed) stop(); else stopWindowObserver = stop
  } catch { /* Window controls remain usable if state observation is unavailable. */ }
})
onBeforeUnmount(() => { disposed = true; stopWindowObserver?.() })

const pageTitle = computed(() => {
  const name = route.name as string
  const titles: Record<string, string> = {
    workspace: t('工作区', 'Workspace'),
    search: t('搜索', 'Search'),
    chat: t('AI 对话', 'AI Chat'),
    agent: t('智能体执行轨迹', 'Agent Trace'),
    tasks: t('任务', 'Tasks'),
    skills: t('Skill 管理', 'Skill Management'),
    plugins: t('Plugin 与 MCP', 'Plugins and MCP'),
    themes: t('主题管理', 'Theme Management'),
    settings: t('设置', 'Settings'),
    'vault-entry': t('选择知识库', 'Select Knowledge Base'),
    community: t('社区目录', 'Community Catalog'),
    benchmarks: 'Benchmark',
    logs: t('运行日志', 'Operation Logs'),
    media: t('音视频转写', 'Media Transcription'),
  }
  return titles[name] || 'OpenNexus'
})

const currentFileName = computed(() => {
  if (route.name !== 'workspace') return pageTitle.value
  if (workspaceStore.activeFile) {
    return workspaceStore.activeFile.name
  }
  return pageTitle.value
})

const isDirty = computed(() => editorStore.saveStatus === 'dirty' || editorStore.saveStatus === 'conflict')

function toggleFromTitlebar(event: MouseEvent) {
  if (desktop && !(event.target as HTMLElement).closest('button')) void toggleMaximizeWindow()
}
</script>

<template>
  <TitleBarFrame :desktop="desktop" :vault-name="workspaceStore.vaultName" :document-title="currentFileName" :dirty="isDirty" :dark="themeStore.isDark" @dblclick="toggleFromTitlebar">

      <button v-if="route.name !== 'vault-entry'" class="icon-btn" :class="{ active: layout.focusMode }" type="button" :aria-pressed="layout.focusMode" :aria-label="layout.focusMode ? t('退出专注模式', 'Exit focus mode') : t('进入专注模式', 'Enter focus mode')" :title="layout.focusMode ? t('退出专注模式', 'Exit focus mode') : t('进入专注模式', 'Enter focus mode')" @click="layout.toggleFocusMode()">
        <ControlIcon :name="layout.focusMode ? 'focusExit' : 'focus'" :size="18" />
      </button>
      <button class="icon-btn" type="button" @click="themeStore.toggleTheme()" :aria-label="themeStore.isDark ? t('切换浅色主题', 'Switch to light theme') : t('切换深色主题', 'Switch to dark theme')" :title="themeStore.isDark ? t('切换浅色主题', 'Switch to light theme') : t('切换深色主题', 'Switch to dark theme')">
        <ControlIcon :name="themeStore.isDark ? 'sun' : 'moon'" :size="18" />
      </button>
      <div v-if="desktop" class="window-controls">
        <button class="win-btn minimize" type="button" :title="t('最小化窗口', 'Minimize window')" :aria-label="t('最小化窗口', 'Minimize window')" @click="minimizeWindow"><ControlIcon name="minimize" /></button>
        <button class="win-btn maximize" type="button" :title="maximized ? t('还原窗口', 'Restore window') : t('最大化窗口', 'Maximize window')" :aria-label="maximized ? t('还原窗口', 'Restore window') : t('最大化窗口', 'Maximize window')" @click="toggleMaximizeWindow"><ControlIcon :name="maximized ? 'restore' : 'maximize'" /></button>
        <button class="win-btn close" type="button" :title="t('关闭窗口', 'Close window')" :aria-label="t('关闭窗口', 'Close window')" @click="requestWindowClose"><ControlIcon name="close" /></button>
      </div>
  </TitleBarFrame>
</template>
