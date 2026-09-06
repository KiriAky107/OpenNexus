<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
const sidebar = ref<HTMLElement | null>(null)
const resizable = computed(() => ['file-tree', 'conversation-list'].includes(props.component ?? ''))
const storageKey = computed(() => props.component === 'conversation-list' ? 'chat-sidebar-width' : 'workspace-sidebar-width')
const width = ref(272)
const maxWidth = ref(520)
let dragging = false
function saveWidth() { try { localStorage.setItem(storageKey.value, String(width.value)) } catch { /* Keep resizing available when storage is unavailable. */ } }
function clampWidth(value: number) { return Math.max(200, Math.min(maxWidth.value, value)) }
function updateBounds() {
  maxWidth.value = Math.max(200, Math.min(520, window.innerWidth - (sidebar.value?.getBoundingClientRect().left ?? 0) - 320))
  width.value = clampWidth(width.value)
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
  width.value = 272
  try { const saved = Number(localStorage.getItem(storageKey.value)); if (saved >= 200 && Number.isFinite(saved)) width.value = saved } catch { /* Use default width. */ }
  updateBounds()
}
watch(() => props.component, () => { dragging = false; restoreWidth() })
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
  <aside ref="sidebar" class="secondary-sidebar" :style="resizable ? { width: `${width}px` } : undefined">
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
    <div v-if="resizable" class="sidebar-resizer" role="separator" aria-orientation="vertical" :aria-label="t('调整侧栏宽度', 'Resize sidebar')" :aria-valuenow="width" :aria-valuemin="200" :aria-valuemax="maxWidth" tabindex="0" @pointerdown="beginResize" @pointermove="resize" @pointerup="endResize" @pointercancel="endResize" @lostpointercapture="endResize" @keydown="resizeWithKeyboard" @dblclick="width = clampWidth(272); saveWidth()" />
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
