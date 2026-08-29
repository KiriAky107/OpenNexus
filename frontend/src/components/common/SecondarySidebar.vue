<script setup lang="ts">
import { computed } from 'vue'
import FileTreePanel from '@/features/workspace/FileTreePanel.vue'
import { useRoute } from 'vue-router'

const props = defineProps<{
  component: string | null
}>()

const route = useRoute()
const routeName = computed(() => route.name as string)

const sidebarTitle = computed(() => {
  const titles: Record<string, string> = {
    'file-tree': '文件',
    'conversation-list': '对话',
    'run-list': 'Agent Run',
    'search-filters': '搜索筛选',
    'task-filters': '任务筛选',
    'extension-list': '扩展',
  }
  return titles[props.component || ''] || ''
})

const showSkillToggle = computed(() => routeName.value === 'skills' || routeName.value === 'plugins')
</script>

<template>
  <aside class="secondary-sidebar">
    <div class="sidebar-header">
      <h3 class="sidebar-title">{{ sidebarTitle }}</h3>
      <div v-if="showSkillToggle" class="sidebar-tabs">
        <router-link to="/extensions/skills" class="tab" :class="{ active: routeName === 'skills' }">Skill</router-link>
        <router-link to="/extensions/plugins" class="tab" :class="{ active: routeName === 'plugins' }">Plugin</router-link>
      </div>
    </div>
    <div class="sidebar-content">
      <FileTreePanel v-if="component === 'file-tree'" />
      <p v-else class="sidebar-placeholder">该功能将在对应页面实现时补充。</p>
    </div>
  </aside>
</template>

<style scoped>
.secondary-sidebar {
  width: var(--sidebar-secondary-width);
  background: var(--color-background-primary);
  border-right: 1px solid var(--color-border-default);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  min-width: 0;
}

.sidebar-header {
  padding: var(--space-md) var(--space-lg);
  border-bottom: 1px solid var(--color-border-subtle);
  flex-shrink: 0;
}

.sidebar-title {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-sm) 0;
}

.sidebar-tabs {
  display: flex;
  gap: 2px;
  background: var(--color-background-secondary);
  padding: 2px;
  border-radius: var(--radius-md);
}

.tab {
  flex: 1;
  text-align: center;
  padding: 4px 8px;
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  text-decoration: none;
  transition: all var(--motion-fast);

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
}

.sidebar-placeholder {
  padding: var(--space-lg);
  color: var(--color-text-tertiary);
  font-size: var(--font-size-sm);
}
</style>
