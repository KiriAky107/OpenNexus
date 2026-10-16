<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useLayoutPreferencesStore } from '@/stores/layoutPreferences'
import { Brush, ChatDotRound, CircleCheck, Connection, Cpu, Document, FolderOpened, Lightning, Monitor, Search, Setting } from '@element-plus/icons-vue'
import PrimaryNavigation from './PrimaryNavigation.vue'
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
const systemOpen = ref(false)
const groups = computed(() => [
  { key: 'knowledge', label: t('知识与学习', 'Knowledge'), items: navItems.value.slice(0, 6) },
  { key: 'extensions', label: t('扩展', 'Extensions'), items: navItems.value.slice(6, 11) },
  { key: 'system', label: t('系统', 'System'), items: navItems.value.slice(11, 13) },
])
watch(currentName, name => { if (['logs', 'benchmarks'].includes(name)) systemOpen.value = true }, { immediate: true })

function navigate(name: string) {
  router.push({ name })
}

function toggleExpanded() {
  expanded.value = !expanded.value
}
</script>

<template>
  <PrimaryNavigation :groups="groups" :current-name="currentName" :expanded="expanded" :system-open="systemOpen"
    @navigate="navigate" @toggle-expanded="toggleExpanded" @toggle-system="systemOpen = !systemOpen" />
</template>
