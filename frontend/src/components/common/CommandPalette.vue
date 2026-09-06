<script setup lang="ts">
import AppDialog from './AppDialog.vue'
import ActionDialog from '@/components/common/ActionDialog.vue'
import { useActionDialog } from '@/composables/useActionDialog'
const { actionDialog, resolveAction, askPrompt } = useActionDialog()
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useEditorStore } from '@/stores/editor'
import { useThemeStore } from '@/stores/theme'
import { useWorkspaceStore } from '@/stores/workspace'
import * as workspaceService from '@/services/workspaceService'
import * as pluginService from '@/services/pluginService'
import type { PluginCommand, PluginCommandEffect } from '@/contracts'
import { usePluginStore } from '@/stores/plugin'
import { t } from '@/i18n'

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
  { id: 'themes', label: t('主题管理', 'Manage themes'), hint: t('导航', 'Navigation'), run: () => router.push('/themes') },
  { id: 'tasks', label: t('任务列表', 'Tasks'), hint: t('导航', 'Navigation'), run: () => router.push('/tasks') },
  { id: 'workspace', label: t('打开工作区', 'Open workspace'), hint: t('导航', 'Navigation'), run: () => router.push('/workspace') },
  { id: 'search', label: t('全局搜索', 'Global search'), hint: t('导航', 'Navigation'), run: () => router.push('/search') },
  { id: 'chat', label: t('打开 AI 对话', 'Open AI chat'), hint: t('导航', 'Navigation'), run: () => router.push('/chat') },
  { id: 'agent', label: t('创建智能体运行', 'Create agent run'), hint: t('导航', 'Navigation'), run: () => router.push('/agent/runs') },
  { id: 'settings', label: t('打开设置', 'Open settings'), hint: t('导航', 'Navigation'), run: () => router.push('/settings') },
  { id: 'mode', label: editorStore.mode === 'source' ? t('切换为写作模式', 'Switch to writing mode') : t('切换为源码模式', 'Switch to source mode'), hint: t('编辑器', 'Editor'), run: () => editorStore.toggleMode() },
  { id: 'save', label: t('保存当前笔记', 'Save current note'), hint: t('编辑器', 'Editor'), run: () => editorStore.save() },
  { id: 'theme', label: themeStore.isDark ? t('切换为浅色主题', 'Switch to light theme') : t('切换为深色主题', 'Switch to dark theme'), hint: t('外观', 'Appearance'), run: () => themeStore.toggleTheme() },
  { id: 'new-note', label: t('创建笔记', 'Create note'), hint: t('工作区', 'Workspace'), run: createNote },
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
  await nextTick()
  try {
    await command.run()
  } catch (error) {
    commandNotice.value = error instanceof Error ? error.message : t('命令执行失败', 'Command failed')
  }
}

async function createNote() {
  const rawName = (await askPrompt(t('笔记名称', 'Note name')))?.trim()
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
    commandError.value = error instanceof Error ? error.message : t('Plugin 命令加载失败', 'Failed to load plugin commands')
  }
}

function hasRequiredArguments(command: PluginCommand) {
  return Array.isArray(command.parameters.required) && command.parameters.required.length > 0
}

async function executePluginCommand(command: PluginCommand) {
  if (hasRequiredArguments(command)) {
    pluginStore.selectPlugin(command.plugin_id)
    await router.push('/extensions/plugins')
    commandNotice.value = `${t('请在 Plugin 详情页填写参数后执行', 'Enter parameters on the Plugin details page, then run')} “${command.title}”.`
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
    commandNotice.value = t('相关数据已刷新。', 'Related data refreshed.')
    return
  }
  if (effect.type === 'job') { commandNotice.value = t('后台任务已创建：', 'Background job created: ') + effect.payload.job_id; return }
  commandNotice.value = t('Plugin 命令执行完成。', 'Plugin command completed.')
}

function handleKeydown(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 'p') {
    if (!open.value && document.querySelector('dialog[open]')) return
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
  <ActionDialog v-if="actionDialog" v-bind="actionDialog" @resolve="resolveAction" />
  <div v-if="commandNotice" class="command-toast" role="status">
    <span>{{ commandNotice }}</span><button :aria-label="t('关闭通知', 'Close notification')" @click="commandNotice = ''">×</button>
  </div>
  <Teleport to="body">
    <AppDialog v-if="open" :label="t('命令面板', 'Command palette')" @close="hide">
      <section class="modal command-palette">
        <input ref="input" v-model="query" class="command-input" :placeholder="t('输入命令…', 'Enter a command…')" @keydown.enter.prevent="execute(filteredCommands[0])" />
        <p v-if="commandError" class="command-error">{{ commandError }}</p>
        <div class="command-list">
          <button v-for="command in filteredCommands" :key="command.id" type="button" @click="execute(command)">
            <span>{{ command.label }}</span><small>{{ command.hint }}</small>
          </button>
          <p v-if="!filteredCommands.length">{{ t('没有匹配的命令', 'No matching commands') }}</p>
        </div>
        <footer><span>Enter · {{ t('执行', 'Run') }}</span><span>Esc · {{ t('关闭', 'Close') }}</span></footer>
      </section>
    </AppDialog>
  </Teleport>
</template>

<style scoped>
.command-palette { padding: 0; display: flex; flex-direction: column; width: min(620px, calc(100vw - 32px)); overflow: hidden; border: 1px solid var(--color-border-default); border-radius: var(--radius-xl); background: var(--color-surface-elevated); box-shadow: var(--shadow-xl); animation: command-palette-in var(--motion-normal) both; }
.command-input { width: 100%; padding: var(--space-xl); border: 0; border-bottom: 1px solid var(--color-border-default); outline: 0; background: transparent; color: var(--color-text-primary); font-size: var(--font-size-xl); }
.command-list { min-height: 0; max-height: 360px; overflow: auto; padding: var(--space-sm); }
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
