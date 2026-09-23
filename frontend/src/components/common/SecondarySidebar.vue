<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import FileTreePanel from '@/features/workspace/FileTreePanel.vue'
import ConversationListPanel from '@/features/chat/ConversationListPanel.vue'
import RunListPanel from '@/features/agent/RunListPanel.vue'
import SearchFiltersPanel from '@/features/search/SearchFiltersPanel.vue'
import TaskFiltersPanel from '@/features/tasks/TaskFiltersPanel.vue'
import ExtensionListPanel from '@/components/common/ExtensionListPanel.vue'
import { useRoute } from 'vue-router'
import { t } from '@/i18n'
import { useLayoutPreferencesStore } from '@/stores/layoutPreferences'
import ControlIcon from './ControlIcon.vue'

const props = defineProps<{
  component: string | null
}>()

const route = useRoute()
const routeName = computed(() => route.name as string)
const sidebar = ref<HTMLElement | null>(null)
const resizable = computed(() => ['file-tree', 'conversation-list'].includes(props.component ?? ''))
const layout = useLayoutPreferencesStore()
const collapsed = computed(() => props.component === 'file-tree' && layout.workspaceCollapsed)
const preferredWidth = computed(() => props.component === 'conversation-list' ? layout.chatWidth : layout.workspaceWidth)
const width = ref(272)
const maxWidth = ref(520)
let dragging = false
function saveWidth() {
  if (props.component === 'conversation-list') layout.chatWidth = width.value
  else layout.workspaceWidth = width.value
}
function clampWidth(value: number) { return Math.max(200, Math.min(maxWidth.value, value)) }
function updateBounds() {
  maxWidth.value = Math.max(200, Math.min(520, window.innerWidth - (sidebar.value?.getBoundingClientRect().left ?? 0) - 320))
  width.value = clampWidth(dragging ? width.value : preferredWidth.value)
}
function beginResize(event: PointerEvent) {
  if (event.button !== 0) return
  event.preventDefault()
  updateBounds()
  dragging = true
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
}
function resize(event: PointerEvent) {
  if (dragging) width.value = clampWidth(event.clientX - (sidebar.value?.getBoundingClientRect().left ?? 0))
}
function endResize() { if (dragging) { dragging = false; saveWidth() } }
function resizeWithKeyboard(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  updateBounds()
  width.value = event.key === 'Home' ? 200 : event.key === 'End' ? maxWidth.value : clampWidth(width.value + (event.key === 'ArrowLeft' ? -16 : 16))
  saveWidth()
}
function restoreWidth() {
  width.value = preferredWidth.value
  updateBounds()
}
watch([() => props.component, preferredWidth, collapsed], () => { dragging = false; restoreWidth() })
watch(collapsed, async value => {
  // Only move focus when the control being hidden belonged to this sidebar.
  const ownedFocus = sidebar.value?.contains(document.activeElement)
  await nextTick()
  if (ownedFocus) sidebar.value?.querySelector<HTMLButtonElement>(value ? '.collapsed-rail button' : '[role="tab"][aria-selected="true"]')?.focus()
})
onMounted(() => {
  restoreWidth()
  window.addEventListener('resize', updateBounds)
})
onBeforeUnmount(() => { endResize(); window.removeEventListener('resize', updateBounds) })

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
  <aside ref="sidebar" class="secondary-sidebar" :class="{ collapsed }" :style="resizable ? { width: collapsed ? '40px' : `${width}px` } : undefined">
    <nav v-if="collapsed" class="collapsed-rail" :aria-label="t('工作区侧栏', 'Workspace sidebar')">
      <button v-for="tab in (['files', 'outline'] as const)" :key="tab" type="button" :title="tab === 'files' ? t('展开文件树', 'Show files') : t('展开大纲树', 'Show outline')" :aria-label="tab === 'files' ? t('展开文件树', 'Show files') : t('展开大纲树', 'Show outline')" :data-panel="tab" @click="layout.showWorkspacePanel(tab)"><ControlIcon :name="tab" /></button>
    </nav>
    <div v-if="component !== 'file-tree'" class="sidebar-header">
      <h3 class="sidebar-title">{{ sidebarTitle }}</h3>
      <div v-if="showSkillToggle" class="sidebar-tabs">
        <router-link to="/extensions/skills" class="tab" :class="{ active: routeName === 'skills' }">Skill</router-link>
        <router-link to="/extensions/plugins" class="tab" :class="{ active: routeName === 'plugins' }">Plugin</router-link>
      </div>
    </div>
    <div v-show="!collapsed" class="sidebar-content" :class="{ 'file-sidebar-content': component === 'file-tree' }">
      <FileTreePanel v-if="component === 'file-tree'" />
      <ConversationListPanel v-else-if="component === 'conversation-list'" />
      <RunListPanel v-else-if="component === 'run-list'" />
      <SearchFiltersPanel v-else-if="component === 'search-filters'" />
      <TaskFiltersPanel v-else-if="component === 'task-filters'" />
      <ExtensionListPanel v-else-if="component === 'extension-list'" />
    </div>
    <div v-if="resizable && !collapsed" class="sidebar-resizer" role="separator" aria-orientation="vertical" :aria-label="t('调整侧栏宽度', 'Resize sidebar')" :aria-valuenow="width" :aria-valuemin="200" :aria-valuemax="maxWidth" tabindex="0" @pointerdown="beginResize" @pointermove="resize" @pointerup="endResize" @pointercancel="endResize" @lostpointercapture="endResize" @keydown="resizeWithKeyboard" @dblclick="width = clampWidth(272); saveWidth()" />
  </aside>
</template>

<style scoped>
.secondary-sidebar {
  position: relative;
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
.collapsed-rail { display: grid; justify-items: center; gap: 6px; padding: 9px 3px; }
.collapsed-rail button { display: grid; place-items: center; width: 32px; height: 34px; border-radius: var(--radius-sm); color: var(--color-text-secondary); }
.collapsed-rail button:hover { background: var(--color-accent-soft); color: var(--color-accent-primary); }
.collapsed-rail button:focus-visible { outline: 2px solid var(--color-border-focus); outline-offset: -2px; }

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
.sidebar-resizer { position: absolute; top: 0; bottom: 0; right: -3px; width: 6px; z-index: 20; cursor: col-resize; touch-action: none; }
.sidebar-resizer:hover, .sidebar-resizer:focus-visible { background: var(--color-accent-secondary); outline: none; }
.file-sidebar-content { min-height: 0; overflow: hidden; scrollbar-gutter: auto; }

</style>
