<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useWorkspaceStore } from '@/stores/workspace'
import { useEditorStore } from '@/stores/editor'
import { useThemeStore } from '@/stores/theme'

const route = useRoute()
const workspaceStore = useWorkspaceStore()
const editorStore = useEditorStore()
const themeStore = useThemeStore()

const pageTitle = computed(() => {
  const name = route.name as string
  const titles: Record<string, string> = {
    workspace: '工作区',
    search: '搜索',
    chat: 'AI 对话',
    agent: 'Agent Trace',
    tasks: '任务',
    skills: 'Skill 管理',
    plugins: 'Plugin 管理',
    themes: '主题管理',
    settings: '设置',
  }
  return titles[name] || '知笔知己'
})

const currentFileName = computed(() => {
  if (route.name !== 'workspace') return pageTitle.value
  if (workspaceStore.activeFile) {
    return workspaceStore.activeFile.name
  }
  return pageTitle.value
})

const isDirty = computed(() => editorStore.saveStatus === 'dirty' || editorStore.saveStatus === 'conflict')
</script>

<template>
  <div class="titlebar">
    <div class="titlebar-left">
      <span class="vault-name">{{ workspaceStore.vaultName }}</span>
      <span class="title-separator">/</span>
      <span class="file-name" :class="{ dirty: isDirty }">
        {{ currentFileName }}
        <span v-if="isDirty" class="dirty-dot" />
      </span>
    </div>
    <div class="titlebar-center">
      <span class="app-name">知笔知己</span>
    </div>
    <div class="titlebar-right">
      <button class="icon-btn" @click="themeStore.toggleTheme()" :title="themeStore.isDark ? '切换浅色主题' : '切换深色主题'">
        <span class="icon">{{ themeStore.isDark ? '☀️' : '🌙' }}</span>
      </button>
      <div class="window-controls">
        <span class="win-btn minimize">—</span>
        <span class="win-btn maximize">▢</span>
        <span class="win-btn close">✕</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.titlebar {
  height: var(--titlebar-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--space-md);
  background: var(--color-background-secondary);
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
  font-weight: 500;
}

.titlebar-right {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  min-width: 200px;
  justify-content: flex-end;
}

.icon-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
  font-size: 14px;
  -webkit-app-region: no-drag;
  transition: background var(--motion-fast);

  &:hover {
    background: var(--color-background-hover);
    color: var(--color-text-primary);
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
  transition: background var(--motion-fast);

  &:hover {
    background: var(--color-background-hover);
  }

  &.close:hover {
    background: var(--color-error);
    color: white;
  }
}
</style>
