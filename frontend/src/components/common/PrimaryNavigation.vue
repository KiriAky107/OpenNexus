<script setup lang="ts">
import type { Component } from 'vue'
import { ArrowLeftBold, ArrowRightBold, Setting } from '@element-plus/icons-vue'
import AppIcon from './AppIcon.vue'
import { t } from '@/i18n'
export interface NavigationGroup { key: string; label: string; items: { name: string; label: string; icon: Component }[] }
defineProps<{ groups: NavigationGroup[]; currentName: string; expanded: boolean; systemOpen: boolean }>()
const emit = defineEmits<{ navigate: [name: string]; toggleExpanded: []; toggleSystem: [] }>()
</script>

<template>
  <aside class="primary-sidebar" :class="{ expanded }">
    <nav class="nav-list" :aria-label="t('主导航', 'Main navigation')">
      <section v-for="group in groups" :key="group.key" class="nav-group">
        <button v-if="group.key === 'system'" type="button" class="group-heading system-toggle" :aria-expanded="systemOpen" :title="group.label" @click="emit('toggleSystem')"><span>{{ expanded ? group.label : '···' }}</span><span v-if="expanded" aria-hidden="true">{{ systemOpen ? '−' : '+' }}</span></button>
        <h2 v-else class="group-heading"><span v-if="expanded">{{ group.label }}</span></h2>
        <div v-show="group.key !== 'system' || systemOpen" class="group-items">
      <button
        type="button"
        v-for="item in group.items"
        :key="item.name"
        class="nav-item"
        :class="{ active: currentName === item.name }"
        :aria-current="currentName === item.name ? 'page' : undefined"
        @click="emit('navigate', item.name)"
        :title="item.label"
      >
        <AppIcon class="nav-icon" :icon="item.icon" :size="20" />
        <span class="nav-label">{{ item.label }}</span>
      </button>
        </div>
      </section>
    </nav>
    <div class="sidebar-footer">
      <button type="button" class="nav-item footer-button" :class="{ active: currentName === 'settings' }" :title="t('设置', 'Settings')" @click="emit('navigate', 'settings')"><AppIcon class="nav-icon" :icon="Setting" :size="20" /><span class="nav-label">{{ t('设置', 'Settings') }}</span></button>
      <button class="nav-item collapse-button" type="button" :title="expanded ? t('收起导航', 'Collapse navigation') : t('展开导航', 'Expand navigation')" @click="emit('toggleExpanded')">
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
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: var(--space-md) 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nav-group { flex-shrink: 0; }
.group-items { display: grid; gap: 2px; }
.group-heading { margin: 8px 16px 6px; font-size: 11px; font-weight: 600; color: var(--color-text-tertiary); min-height: 6px; }
.system-toggle { width: calc(100% - 32px); display: flex; justify-content: space-between; align-items: center; min-height: 28px; }
.footer-button { width: calc(100% - 12px); }
.expanded .nav-item { height: 40px; }

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
