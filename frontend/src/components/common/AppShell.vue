<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useWorkspaceStore } from '@/stores/workspace'
import { useThemeStore } from '@/stores/theme'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { useAgentStore } from '@/stores/agent'
import PrimarySidebar from './PrimarySidebar.vue'
import SecondarySidebar from './SecondarySidebar.vue'
import StatusBar from './StatusBar.vue'
import TitleBar from './TitleBar.vue'

defineProps<{
  showSecondarySidebar?: boolean
}>()

const workspaceStore = useWorkspaceStore()
const themeStore = useThemeStore()
const editorStore = useEditorStore()
const settingsStore = useSettingsStore()
const agentStore = useAgentStore()
const route = useRoute()
const router = useRouter()

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

function openCitation(noteId: string, blockId: string, filePath: string) {
  workspaceStore.openFile(filePath)
  editorStore.highlightBlock(blockId)
  router.push('/workspace')
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
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  width: 100vw;
  background: var(--color-background-primary);
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
}
</style>
