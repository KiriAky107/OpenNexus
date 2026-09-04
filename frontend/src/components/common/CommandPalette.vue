<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useEditorStore } from '@/stores/editor'
import { useThemeStore } from '@/stores/theme'
import { useWorkspaceStore } from '@/stores/workspace'
import * as workspaceService from '@/services/workspaceService'
import * as pluginService from '@/services/pluginService'
import type { PluginCommand, PluginCommandEffect } from '@/contracts'
import { usePluginStore } from '@/stores/plugin'

const router = useRouter()
const editorStore = useEditorStore()
const themeStore = useThemeStore()
const workspaceStore = useWorkspaceStore()
const pluginStore = usePluginStore()
const open = ref(false)
const query = ref('')
const input = ref<HTMLInputElement | null>(null)
const pluginCommands = ref<PluginCommand[]>([])
const commandError = ref('')
const commandNotice = ref('')
const selectionSnapshot = ref<string | null>(null)

interface Command { id: string; label: string; hint: string; run: () => void | Promise<void> }

const builtinCommands = computed<Command[]>(() => [
  { id: 'workspace', label: '打开工作区', hint: '导航', run: () => router.push('/workspace') },
  { id: 'search', label: '全局搜索', hint: '导航', run: () => router.push('/search') },
  { id: 'chat', label: '打开 AI 对话', hint: '导航', run: () => router.push('/chat') },
  { id: 'agent', label: '创建智能体运行', hint: '导航', run: () => router.push('/agent/runs') },
  { id: 'themes', label: '主题管理', hint: '导航', run: () => router.push('/themes') },
  { id: 'settings', label: '打开设置', hint: '导航', run: () => router.push('/settings') },
  { id: 'tasks', label: '任务列表', hint: '导航', run: () => router.push('/tasks') },
  { id: 'mode', label: `切换为${editorStore.mode === 'source' ? '写作' : '源码'}模式`, hint: '编辑器', run: () => editorStore.toggleMode() },
  { id: 'save', label: '保存当前笔记', hint: '编辑器', run: () => editorStore.save() },
  { id: 'theme', label: `切换为${themeStore.isDark ? '浅色' : '深色'}主题`, hint: '外观', run: () => themeStore.toggleTheme() },
  { id: 'new-note', label: '创建笔记', hint: '工作区', run: createNote },
])

const commands = computed<Command[]>(() => [
  ...builtinCommands.value,
  ...pluginCommands.value.filter(isPluginCommandAvailable).map((command) => ({
    id: 'plugin:' + command.command_id,
    label: command.title,
    hint: 'Plugin · ' + command.plugin_id,
    run: () => executePluginCommand(command),
  })),
])

function isPluginCommandAvailable(command: PluginCommand) {
  if (!command.enabled) return false
  return command.when.every((condition) => {
    if (condition === 'workspace.has_vault') return Boolean(workspaceStore.vaultId)
    if (condition === 'editor.has_note') return Boolean(editorStore.currentNoteId)
    if (condition === 'editor.has_selection') return Boolean(selectionSnapshot.value)
    return false
  })
}

const filteredCommands = computed(() => {
  const value = query.value.trim().toLocaleLowerCase()
  return value ? commands.value.filter((command) => `${command.label} ${command.hint}`.toLocaleLowerCase().includes(value)) : commands.value
})


function show() {
  selectionSnapshot.value = window.getSelection()?.toString() || null
  open.value = true
  query.value = ''
  commandError.value = ''
  void loadPluginCommands()
  void nextTick(() => input.value?.focus())
}

function hide() { open.value = false }

async function execute(command: Command | undefined) {
  if (!command) return
  hide()
  try {
    await command.run()
  } catch (error) {
    commandNotice.value = error instanceof Error ? error.message : '命令执行失败'
  }
}

async function createNote() {
  const rawName = window.prompt('笔记名称')?.trim()
  if (!rawName) return
  const name = rawName.endsWith('.md') ? rawName : `${rawName}.md`
  const file = await workspaceService.createFile('/', name, `# ${rawName}\n\n`)
  workspaceStore.addFileToTree('/', file)
  await editorStore.loadFile(file.path)
  workspaceStore.openFile(file.path)
  await router.push('/workspace')
}

async function loadPluginCommands() {
  try {
    pluginCommands.value = await pluginService.listPluginCommands('command_palette')
  } catch (error) {
    commandError.value = error instanceof Error ? error.message : 'Plugin 命令加载失败'
  }
}

function hasRequiredArguments(command: PluginCommand) {
  return Array.isArray(command.parameters.required) && command.parameters.required.length > 0
}

async function executePluginCommand(command: PluginCommand) {
  if (hasRequiredArguments(command)) {
    pluginStore.selectPlugin(command.plugin_id)
    await router.push('/extensions/plugins')
    commandNotice.value = '请在 Plugin 详情页填写参数后执行“' + command.title + '”。'
    return
  }
  const result = await pluginService.executePluginCommand(command.command_id, {}, {
    vault_id: workspaceStore.hasVault ? workspaceStore.vaultId : null,
    note_id: editorStore.currentNoteId,
    file_path: editorStore.currentFilePath,
    selection: selectionSnapshot.value,
  })
  await applyPluginEffect(result.effect)
}

async function applyPluginEffect(effect: PluginCommandEffect) {
  if (effect.type === 'notification') { commandNotice.value = effect.payload.message; return }
  if (effect.type === 'navigate') {
    const routes: Record<string, string> = {
      'vault-entry': '/', workspace: '/workspace', search: '/search', chat: '/chat',
      agent: '/agent/runs', tasks: '/tasks', skills: '/extensions/skills',
      plugins: '/extensions/plugins', themes: '/themes', settings: '/settings',
    }
    await router.push(routes[effect.payload.route])
    return
  }
  if (effect.type === 'refresh') {
    if (effect.payload.scope === 'plugins') await pluginStore.loadPlugins()
    if (effect.payload.scope === 'commands') await loadPluginCommands()
    commandNotice.value = '相关数据已刷新。'
    return
  }
  if (effect.type === 'job') { commandNotice.value = '后台任务已创建：' + effect.payload.job_id; return }
  commandNotice.value = 'Plugin 命令执行完成。'
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
  <div v-if="commandNotice" class="command-toast" role="status">
    <span>{{ commandNotice }}</span><button aria-label="关闭通知" @click="commandNotice = ''">×</button>
  </div>
  <Teleport to="body">
    <div v-if="open" class="command-backdrop" @click.self="hide">
      <section class="command-palette" role="dialog" aria-modal="true" aria-label="命令面板">
        <input ref="input" v-model="query" class="command-input" placeholder="输入命令…" @keydown.enter.prevent="execute(filteredCommands[0])" />
        <p v-if="commandError" class="command-error">{{ commandError }}</p>
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
.command-backdrop { position: fixed; inset: 0; z-index: var(--z-modal); display: flex; justify-content: center; align-items: flex-start; padding-top: 12vh; background: var(--color-background-overlay); animation: command-backdrop-in var(--motion-fast) both; }
.command-palette { width: min(620px, calc(100vw - 32px)); overflow: hidden; border: 1px solid var(--color-border-default); border-radius: var(--radius-xl); background: var(--color-surface-elevated); box-shadow: var(--shadow-xl); animation: command-palette-in var(--motion-normal) both; }
.command-input { width: 100%; padding: var(--space-xl); border: 0; border-bottom: 1px solid var(--color-border-default); outline: 0; background: transparent; color: var(--color-text-primary); font-size: var(--font-size-xl); }
.command-list { max-height: 360px; overflow: auto; padding: var(--space-sm); }
.command-list button { display: flex; justify-content: space-between; width: 100%; padding: var(--space-md) var(--space-lg); border-radius: var(--radius-md); text-align: left; transition: color var(--motion-fast), background-color var(--motion-fast), transform var(--motion-fast); }
.command-list button:hover, .command-list button:focus { outline: 0; background: var(--color-accent-soft); color: var(--color-accent-primary); }
.command-list button:hover { transform: translateX(2px); }
.command-list small, .command-list p, footer { color: var(--color-text-tertiary); }
.command-list p { padding: var(--space-xl); text-align: center; }
footer { display: flex; gap: var(--space-lg); padding: var(--space-sm) var(--space-lg); border-top: 1px solid var(--color-border-subtle); font-size: var(--font-size-xs); }
.command-error { margin: var(--space-sm); padding: var(--space-sm) var(--space-md); border-radius: var(--radius-md); background: var(--color-error-soft); color: var(--color-error); font-size: var(--font-size-sm); }
.command-toast { position: fixed; top: 48px; right: var(--space-xl); z-index: calc(var(--z-modal) + 1); display: flex; align-items: center; gap: var(--space-lg); max-width: min(420px, calc(100vw - 32px)); padding: var(--space-md) var(--space-lg); border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-surface-elevated); box-shadow: var(--shadow-lg); animation: notice-in var(--motion-normal) both; }
.command-toast button { color: var(--color-text-tertiary); font-size: var(--font-size-xl); }

@keyframes command-backdrop-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes command-palette-in { from { opacity: 0; transform: translateY(-8px) scale(.99); } to { opacity: 1; transform: translateY(0) scale(1); } }
</style>
