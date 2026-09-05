<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useWorkspaceStore } from '@/stores/workspace'
import { useThemeStore } from '@/stores/theme'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import PrimarySidebar from './PrimarySidebar.vue'
import SecondarySidebar from './SecondarySidebar.vue'
import StatusBar from './StatusBar.vue'
import TitleBar from './TitleBar.vue'
import CommandPalette from './CommandPalette.vue'
import { getIndexStatus } from '@/services/indexService'
import { navigateToCitation } from '@/composables/useCitationNavigation'

defineProps<{
  showSecondarySidebar?: boolean
}>()

const workspaceStore = useWorkspaceStore()
const themeStore = useThemeStore()
const editorStore = useEditorStore()
const settingsStore = useSettingsStore()
const route = useRoute()
const router = useRouter()

let statusTimer: ReturnType<typeof setTimeout> | undefined
let disposed = false
async function pollIndex() {
  try { settingsStore.indexStatus = await getIndexStatus() } catch { /* retain last status; retry */ }
  if (!disposed) statusTimer = setTimeout(pollIndex, 5000)
}
onMounted(() => { void settingsStore.loadDiagnostics(); void pollIndex() })
onUnmounted(() => { disposed = true; clearTimeout(statusTimer) })
watch(() => settingsStore.defaultEditorMode, (mode) => editorStore.setMode(mode), { immediate: true })
watch(() => settingsStore.editorLineWidth, (width) => {
  document.documentElement.style.setProperty('--editor-line-width', `${width}ch`)
}, { immediate: true })

const routeName = computed(() => route.name as string)

const secondaryComponent = computed(() => {
  switch (routeName.value) {
    case 'workspace': return 'file-tree'
    case 'search': return 'search-filters'
    case 'chat': return 'conversation-list'
    case 'agent': return 'run-list'
    case 'tasks': return 'task-filters'
    case 'skills':
    case 'plugins': return 'extension-list'
    default: return null
  }
})

function openCitation(_noteId: string, blockId: string, filePath: string) {
  // 走统一的定位流程：必须先 loadFile 再 highlightBlock，
  // 否则 editor store 的 loadFile 会把刚设好的高亮清掉。
  return navigateToCitation(
    { file_path: filePath, block_id: blockId },
    {
      loadFile: (path) => editorStore.loadFile(path),
      openFile: (path) => workspaceStore.openFile(path),
      highlightBlock: (id) => editorStore.highlightBlock(id),
      navigate: (path) => router.push(path),
    },
  )
}

defineExpose({ openCitation })
</script>

<template>
  <div class="app-shell" :class="{ 'theme-dark': themeStore.isDark }">
    <TitleBar />
    <div class="app-body">
      <PrimarySidebar />
      <SecondarySidebar v-if="secondaryComponent" :component="secondaryComponent" />
      <main class="main-content">
        <slot />
      </main>
    </div>
    <StatusBar />
    <CommandPalette />
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  width: 100vw;
  background: var(--color-background-secondary);
  color: var(--color-text-primary);
}

.app-body {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
}

.main-content {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  background: var(--color-background-primary);
  isolation: isolate;
}
</style>
