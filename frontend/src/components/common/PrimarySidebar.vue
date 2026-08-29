<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { computed, ref } from 'vue'

const route = useRoute()
const router = useRouter()
const expanded = ref(localStorage.getItem('primary-sidebar-expanded') === 'true')

const navItems = [
  { name: 'workspace', icon: '📁', label: '工作区' },
  { name: 'search', icon: '🔍', label: '搜索' },
  { name: 'chat', icon: '💬', label: 'AI 对话' },
  { name: 'agent', icon: '🤖', label: 'Agent' },
  { name: 'tasks', icon: '✅', label: '任务' },
  { name: 'skills', icon: '⚡', label: 'Skill' },
  { name: 'plugins', icon: '🧩', label: 'Plugin' },
  { name: 'themes', icon: '🎨', label: '主题' },
  { name: 'settings', icon: '⚙️', label: '设置' },
]

const currentName = computed(() => {
  return route.name as string
})

function navigate(name: string) {
  router.push({ name })
}

function toggleExpanded() {
  expanded.value = !expanded.value
  localStorage.setItem('primary-sidebar-expanded', String(expanded.value))
}
</script>

<template>
  <aside class="primary-sidebar" :class="{ expanded }">
    <nav class="nav-list">
      <div
        v-for="item in navItems"
        :key="item.name"
        class="nav-item"
        :class="{ active: currentName === item.name }"
        @click="navigate(item.name)"
        :title="item.label"
      >
        <span class="nav-icon">{{ item.icon }}</span>
        <span class="nav-label">{{ item.label }}</span>
      </div>
    </nav>
    <div class="sidebar-footer">
      <button class="nav-item collapse-button" type="button" :title="expanded ? '收起导航' : '展开导航'" @click="toggleExpanded">
        <span class="nav-icon">{{ expanded ? '«' : '»' }}</span>
        <span class="nav-label">{{ expanded ? '收起' : '展开' }}</span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.primary-sidebar {
  width: 56px;
  background: var(--color-background-secondary);
  border-right: 1px solid var(--color-border-subtle);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  z-index: var(--z-sidebar);
}

.primary-sidebar.expanded { width: var(--sidebar-primary-width-expanded); }
.primary-sidebar.expanded .nav-item { flex-direction: row; justify-content: flex-start; gap: var(--space-md); padding: 0 var(--space-lg); }
.primary-sidebar.expanded .nav-icon { margin-bottom: 0; }
.primary-sidebar.expanded .nav-label { font-size: var(--font-size-sm); }

.nav-list {
  flex: 1;
  padding: var(--space-sm) 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 50px;
  margin: 0 4px;
  border-radius: var(--radius-md);
  cursor: pointer;
  color: var(--color-text-secondary);
  transition: all var(--motion-fast);
  position: relative;

  &:hover {
    background: var(--color-background-hover);
    color: var(--color-text-primary);
  }

  &.active {
    background: var(--color-accent-soft);
    color: var(--color-accent-primary);

    &::before {
      content: '';
      position: absolute;
      left: -4px;
      top: 50%;
      transform: translateY(-50%);
      width: 3px;
      height: 24px;
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
}

.sidebar-footer {
  padding: var(--space-sm) 0;
  border-top: 1px solid var(--color-border-subtle);
}

.collapse-button { width: calc(100% - 8px); }
</style>
