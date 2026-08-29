<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useEditorStore } from '@/stores/editor'
import { useThemeStore } from '@/stores/theme'
import { useWorkspaceStore } from '@/stores/workspace'
import * as workspaceService from '@/services/workspaceService'

const router = useRouter()
const editorStore = useEditorStore()
const themeStore = useThemeStore()
const workspaceStore = useWorkspaceStore()
const open = ref(false)
const query = ref('')
const input = ref<HTMLInputElement | null>(null)

interface Command { id: string; label: string; hint: string; run: () => void | Promise<void> }

const commands = computed<Command[]>(() => [
  { id: 'workspace', label: '打开工作区', hint: '导航', run: () => router.push('/workspace') },
  { id: 'search', label: '全局搜索', hint: '导航', run: () => router.push('/search') },
  { id: 'chat', label: '打开 AI 对话', hint: '导航', run: () => router.push('/chat') },
  { id: 'agent', label: '创建 Agent Run', hint: '导航', run: () => router.push('/agent/runs') },
  { id: 'settings', label: '打开设置', hint: '导航', run: () => router.push('/settings') },
  { id: 'mode', label: `切换为${editorStore.mode === 'source' ? '写作' : '源码'}模式`, hint: '编辑器', run: () => editorStore.toggleMode() },
  { id: 'save', label: '保存当前笔记', hint: '编辑器', run: () => editorStore.save() },
  { id: 'theme', label: `切换为${themeStore.isDark ? '浅色' : '深色'}主题`, hint: '外观', run: () => themeStore.toggleTheme() },
  { id: 'new-note', label: '创建笔记', hint: '工作区', run: createNote },
])

const filteredCommands = computed(() => {
  const value = query.value.trim().toLocaleLowerCase()
  return value ? commands.value.filter((command) => `${command.label} ${command.hint}`.toLocaleLowerCase().includes(value)) : commands.value
})

function show() {
  open.value = true
  query.value = ''
  void nextTick(() => input.value?.focus())
}

function hide() { open.value = false }

async function execute(command: Command | undefined) {
  if (!command) return
  hide()
  await command.run()
}

async function createNote() {
  const rawName = window.prompt('笔记名称')?.trim()
  if (!rawName) return
  const name = rawName.endsWith('.md') ? rawName : `${rawName}.md`
  const file = await workspaceService.createFile('/', name, `# ${rawName}\n\n`)
  workspaceStore.addFileToTree('/', file)
  workspaceStore.openFile(file.path)
  await editorStore.loadFile(file.path)
  await router.push('/workspace')
}

function handleKeydown(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 'p') {
    event.preventDefault()
    open.value ? hide() : show()
  } else if (event.key === 'Escape' && open.value) {
    hide()
  }
}

onMounted(() => window.addEventListener('keydown', handleKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', handleKeydown))
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="command-backdrop" @click.self="hide">
      <section class="command-palette" role="dialog" aria-modal="true" aria-label="命令面板">
        <input ref="input" v-model="query" class="command-input" placeholder="输入命令…" @keydown.enter.prevent="execute(filteredCommands[0])" />
        <div class="command-list">
          <button v-for="command in filteredCommands" :key="command.id" type="button" @click="execute(command)">
            <span>{{ command.label }}</span><small>{{ command.hint }}</small>
          </button>
          <p v-if="!filteredCommands.length">没有匹配的命令</p>
        </div>
        <footer><span>Enter 执行</span><span>Esc 关闭</span></footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.command-backdrop { position: fixed; inset: 0; z-index: var(--z-modal); display: flex; justify-content: center; align-items: flex-start; padding-top: 12vh; background: var(--color-background-overlay); }
.command-palette { width: min(600px, calc(100vw - 32px)); overflow: hidden; border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-surface-elevated); box-shadow: var(--shadow-xl); }
.command-input { width: 100%; padding: var(--space-lg); border: 0; border-bottom: 1px solid var(--color-border-default); outline: 0; background: transparent; font-size: var(--font-size-xl); }
.command-list { max-height: 360px; overflow: auto; padding: var(--space-sm); }
.command-list button { display: flex; justify-content: space-between; width: 100%; padding: var(--space-md); border-radius: var(--radius-md); text-align: left; }
.command-list button:hover, .command-list button:focus { outline: 0; background: var(--color-accent-soft); color: var(--color-accent-primary); }
.command-list small, .command-list p, footer { color: var(--color-text-tertiary); }
.command-list p { padding: var(--space-xl); text-align: center; }
footer { display: flex; gap: var(--space-lg); padding: var(--space-sm) var(--space-lg); border-top: 1px solid var(--color-border-subtle); font-size: var(--font-size-xs); }
</style>
