<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { computed } from 'vue'

const route = useRoute()
const router = useRouter()

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
  const name = route.name as string
  if (name === 'skills' || name === 'plugins') return 'skills'
  return name
})

function navigate(name: string) {
  router.push({ name })
}
</script>

<template>
  <aside class="primary-sidebar">
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
      <div class="nav-item" @click="navigate('settings')" title="设置">
        <span class="nav-icon">⚙️</span>
      </div>
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
</style>
