<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useWorkspaceStore } from '@/stores/workspace'
import { useEditorStore } from '@/stores/editor'
import { useThemeStore } from '@/stores/theme'
import { Moon, Sunny } from '@element-plus/icons-vue'
import AppIcon from './AppIcon.vue'
import { t } from '@/i18n'
import { isDesktop } from '@/services/platform/desktop'
import { minimizeWindow, requestWindowClose, toggleMaximizeWindow } from '@/services/platform/windowControls'

const route = useRoute()
const workspaceStore = useWorkspaceStore()
const editorStore = useEditorStore()
const themeStore = useThemeStore()
const desktop = isDesktop()

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
  <header class="titlebar" :class="{ desktop }" data-tauri-drag-region @dblclick="toggleFromTitlebar">
    <div class="titlebar-left" data-tauri-drag-region>
      <span v-if="workspaceStore.vaultName" class="vault-name" data-tauri-drag-region>{{ workspaceStore.vaultName }}</span>
      <span v-if="workspaceStore.vaultName" class="title-separator" data-tauri-drag-region>/</span>
      <span class="file-name" :class="{ dirty: isDirty }" data-tauri-drag-region>
        {{ currentFileName }}
        <span v-if="isDirty" class="dirty-dot" />
      </span>
    </div>
    <div class="titlebar-center" data-tauri-drag-region>
      <span class="app-name" data-tauri-drag-region>OpenNexus</span>
    </div>
    <div class="titlebar-right">
      <button class="icon-btn" @click="themeStore.toggleTheme()" :title="themeStore.isDark ? t('切换浅色主题', 'Switch to light theme') : t('切换深色主题', 'Switch to dark theme')">
        <AppIcon :icon="themeStore.isDark ? Sunny : Moon" :size="16" />
      </button>
      <div v-if="desktop" class="window-controls">
        <button class="win-btn minimize" type="button" :aria-label="t('最小化窗口', 'Minimize window')" @click="minimizeWindow">—</button>
        <button class="win-btn maximize" type="button" :aria-label="t('最大化或还原窗口', 'Maximize or restore window')" @click="toggleMaximizeWindow">▢</button>
        <button class="win-btn close" type="button" :aria-label="t('关闭窗口', 'Close window')" @click="requestWindowClose">✕</button>
      </div>
    </div>
  </header>
</template>

<style scoped>
.titlebar {
  height: var(--titlebar-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--space-lg);
  background: var(--color-surface-secondary);
  border-bottom: 1px solid var(--color-border-subtle);
  font-size: var(--font-size-sm);
  flex-shrink: 0;
  z-index: var(--z-titlebar);
  user-select: none;
  -webkit-app-region: drag;
}

.titlebar-left {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  min-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.vault-name {
  color: var(--color-text-secondary);
  font-weight: 500;
}

.title-separator {
  color: var(--color-text-tertiary);
}

.file-name {
  color: var(--color-text-primary);
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;

  &.dirty {
    color: var(--color-accent-primary);
  }
}

.dirty-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-accent-primary);
  flex-shrink: 0;
}

.titlebar-center {
  color: var(--color-text-tertiary);
  font-size: var(--font-size-sm);
}

.app-name {
  padding: 3px 10px;
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-full);
  background: var(--color-surface-primary);
  color: var(--color-text-secondary);
  font-weight: 650;
  letter-spacing: .04em;
}

.titlebar-right {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  min-width: 200px;
  justify-content: flex-end;
}

.icon-btn {
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
  font-size: 14px;
  -webkit-app-region: no-drag;
  transition: background-color var(--motion-fast), color var(--motion-fast), transform var(--motion-fast);

  &:hover {
    background: var(--color-background-hover);
    color: var(--color-text-primary);
    transform: rotate(8deg);
  }
}

.icon {
  font-size: 14px;
}

.window-controls {
  display: flex;
  align-items: center;
  gap: 1px;
  -webkit-app-region: no-drag;
}

.win-btn {
  width: 34px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  padding: 0;
  border: 0;
  background: transparent;
  transition: background-color var(--motion-fast), color var(--motion-fast);

  &:hover {
    background: var(--color-background-hover);
  }

  &.close:hover {
    background: var(--color-error);
    color: var(--color-on-error);
  }
}

@media (max-width: 760px) {
  .titlebar-left, .titlebar-right { min-width: 0; }
  .titlebar-center, .window-controls { display: none; }
  .file-name { max-width: 42vw; }
}
</style>
