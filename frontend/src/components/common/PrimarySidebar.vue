<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useLayoutPreferencesStore } from '@/stores/layoutPreferences'
import { ArrowLeftBold, ArrowRightBold, Brush, ChatDotRound, CircleCheck, Connection, Cpu, Document, FolderOpened, Lightning, Monitor, Search, Setting } from '@element-plus/icons-vue'
import AppIcon from './AppIcon.vue'
import { t } from '@/i18n'

const route = useRoute()
const router = useRouter()
const { primaryExpanded: expanded } = storeToRefs(useLayoutPreferencesStore())

const navItems = computed(() => [
  { name: 'workspace', icon: FolderOpened, label: t('工作区', 'Workspace') },
  { name: 'search', icon: Search, label: t('搜索', 'Search') },
  { name: 'chat', icon: ChatDotRound, label: t('AI 对话', 'AI Chat') },
  { name: 'agent', icon: Cpu, label: t('智能体', 'Agent') },
  { name: 'tasks', icon: CircleCheck, label: t('任务', 'Tasks') },
  { name: 'media', icon: Monitor, label: t('音视频', 'Media') },
  { name: 'skills', icon: Lightning, label: 'Skill' },
  { name: 'plugins', icon: Connection, label: 'Plugin' },
  { name: 'mcp-servers', icon: Monitor, label: 'MCP' },
  { name: 'themes', icon: Brush, label: t('主题', 'Themes') },
  { name: 'community', icon: Connection, label: t('社区', 'Community') },
  { name: 'benchmarks', icon: Monitor, label: 'Benchmark' },
  { name: 'logs', icon: Document, label: t('日志', 'Logs') },
  { name: 'settings', icon: Setting, label: t('设置', 'Settings') },
])

const currentName = computed(() => {
  return route.name as string
})

function navigate(name: string) {
  router.push({ name })
}

function toggleExpanded() {
  expanded.value = !expanded.value
}
</script>

<template>
  <aside class="primary-sidebar" :class="{ expanded }">
    <nav class="nav-list">
      <button
        type="button"
        v-for="item in navItems"
        :key="item.name"
        class="nav-item"
        :class="{ active: currentName === item.name }"
        @click="navigate(item.name)"
        :title="item.label"
      >
        <AppIcon class="nav-icon" :icon="item.icon" :size="20" />
        <span class="nav-label">{{ item.label }}</span>
      </button>
    </nav>
    <div class="sidebar-footer">
      <button class="nav-item collapse-button" type="button" :title="expanded ? t('收起导航', 'Collapse navigation') : t('展开导航', 'Expand navigation')" @click="toggleExpanded">
        <AppIcon class="nav-icon" :icon="expanded ? ArrowLeftBold : ArrowRightBold" />
        <span class="nav-label">{{ expanded ? t('收起', 'Collapse') : t('展开', 'Expand') }}</span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.primary-sidebar {
  width: var(--sidebar-primary-width);
  background: var(--color-background-secondary);
  border-right: 1px solid var(--color-border-subtle);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  z-index: var(--z-sidebar);
  transition: width var(--motion-normal), background-color var(--motion-normal);
}

.primary-sidebar.expanded { width: var(--sidebar-primary-width-expanded); }
.primary-sidebar.expanded .nav-item { flex-direction: row; justify-content: flex-start; gap: var(--space-md); padding: 0 var(--space-lg); }
.primary-sidebar.expanded .nav-icon { margin-bottom: 0; }
.primary-sidebar.expanded .nav-label { font-size: var(--font-size-sm); }

.nav-list {
  flex: 1;
  padding: var(--space-md) 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-item {
  border: 0;
  background: transparent;
  font: inherit;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 48px;
  margin: 0 6px;
  border-radius: var(--radius-md);
  cursor: pointer;
  color: var(--color-text-secondary);
  border: 1px solid transparent;
  transition: color var(--motion-fast), background-color var(--motion-fast), border-color var(--motion-fast), transform var(--motion-fast);
  position: relative;

  &:hover {
    background: var(--color-background-hover);
    color: var(--color-text-primary);
    transform: translateX(2px);
  }

  &.active {
    background: var(--color-accent-soft);
    color: var(--color-accent-primary);
    border-color: color-mix(in srgb, var(--color-accent-primary) 16%, transparent);

    &::before {
      content: '';
      position: absolute;
      left: -7px;
      top: 50%;
      transform: translateY(-50%);
      width: 4px;
      height: 22px;
      border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
      background: var(--color-accent-primary);
    }
  }
}

.nav-icon {
  font-size: 20px;
  line-height: 1;
  margin-bottom: 2px;
}

.nav-label {
  font-size: 10px;
  line-height: 1.2;
  font-weight: 550;
}

.sidebar-footer {
  padding: var(--space-sm) 0;
  border-top: 1px solid var(--color-border-subtle);
}

.collapse-button { width: calc(100% - 12px); }
</style>
