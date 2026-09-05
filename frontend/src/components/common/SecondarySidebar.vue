<script setup lang="ts">
import { computed } from 'vue'
import FileTreePanel from '@/features/workspace/FileTreePanel.vue'
import ConversationListPanel from '@/features/chat/ConversationListPanel.vue'
import RunListPanel from '@/features/agent/RunListPanel.vue'
import SearchFiltersPanel from '@/features/search/SearchFiltersPanel.vue'
import TaskFiltersPanel from '@/features/tasks/TaskFiltersPanel.vue'
import ExtensionListPanel from '@/components/common/ExtensionListPanel.vue'
import { useRoute } from 'vue-router'
import { t } from '@/i18n'

const props = defineProps<{
  component: string | null
}>()

const route = useRoute()
const routeName = computed(() => route.name as string)

const sidebarTitle = computed(() => {
  const titles: Record<string, string> = {
    'file-tree': t('文件', 'Files'),
    'conversation-list': t('对话', 'Conversations'),
    'run-list': t('智能体运行', 'Agent Runs'),
    'search-filters': t('搜索筛选', 'Search Filters'),
    'task-filters': t('任务筛选', 'Task Filters'),
    'extension-list': t('扩展', 'Extensions'),
  }
  return titles[props.component || ''] || ''
})

const showSkillToggle = computed(() => routeName.value === 'skills' || routeName.value === 'plugins')
</script>

<template>
  <aside class="secondary-sidebar">
    <div v-if="component !== 'file-tree'" class="sidebar-header">
      <h3 class="sidebar-title">{{ sidebarTitle }}</h3>
      <div v-if="showSkillToggle" class="sidebar-tabs">
        <router-link to="/extensions/skills" class="tab" :class="{ active: routeName === 'skills' }">Skill</router-link>
        <router-link to="/extensions/plugins" class="tab" :class="{ active: routeName === 'plugins' }">Plugin</router-link>
      </div>
    </div>
    <div class="sidebar-content" :class="{ 'file-sidebar-content': component === 'file-tree' }">
      <FileTreePanel v-if="component === 'file-tree'" />
      <ConversationListPanel v-else-if="component === 'conversation-list'" />
      <RunListPanel v-else-if="component === 'run-list'" />
      <SearchFiltersPanel v-else-if="component === 'search-filters'" />
      <TaskFiltersPanel v-else-if="component === 'task-filters'" />
      <ExtensionListPanel v-else-if="component === 'extension-list'" />
    </div>
  </aside>
</template>

<style scoped>
.secondary-sidebar {
  width: var(--sidebar-secondary-width);
  background: var(--color-surface-secondary);
  border-right: 1px solid var(--color-border-default);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  min-width: 0;
}

.sidebar-header {
  padding: var(--space-lg);
  border-bottom: 1px solid var(--color-border-subtle);
  flex-shrink: 0;
}

.sidebar-title {
  font-size: var(--font-size-lg);
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-md) 0;
}

.sidebar-tabs {
  display: flex;
  gap: 2px;
  background: var(--color-background-secondary);
  padding: 3px;
  border-radius: var(--radius-md);
}

.tab {
  flex: 1;
  text-align: center;
  padding: 6px 8px;
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  text-decoration: none;
  transition: color var(--motion-fast), background-color var(--motion-fast), box-shadow var(--motion-fast);

  &.active {
    background: var(--color-surface-primary);
    color: var(--color-text-primary);
    box-shadow: var(--shadow-sm);
  }

  &:hover {
    color: var(--color-text-primary);
  }
}

.sidebar-content {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-gutter: stable;
}
.file-sidebar-content { min-height: 0; overflow: hidden; scrollbar-gutter: auto; }

</style>
