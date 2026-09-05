<script setup lang="ts">
import { t } from '@/i18n'
import { Refresh, VideoPlay } from '@element-plus/icons-vue'
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '@/components/common/AppIcon.vue'
import type { Plugin, PluginCommand } from '@/contracts'
import * as pluginService from '@/services/pluginService'
import {
  applyCommandEffect,
  cleanArguments,
  coerceArgument,
  commandFields,
  initialArguments,
  missingRequiredFields,
  type CommandField,
} from '@/services/pluginCommandForm'
import { useEditorStore } from '@/stores/editor'
import { usePluginStore } from '@/stores/plugin'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ plugin: Plugin }>()
const emit = defineEmits<{ (e: 'refresh-settings'): void }>()

const router = useRouter()
const pluginStore = usePluginStore()
const editorStore = useEditorStore()
const workspaceStore = useWorkspaceStore()

const commands = ref<PluginCommand[]>([])
const argumentsByCommand = ref<Record<string, Record<string, unknown>>>({})
const loading = ref(false)
const busy = ref('')
const error = ref('')
const notice = ref('')
let loadVersion = 0

watch(() => props.plugin.plugin_id, () => { void load() }, { immediate: true })

async function load() {
  const version = ++loadVersion
  const pluginId = props.plugin.plugin_id
  error.value = ''
  loading.value = true
  try {
    const all = await pluginService.listPluginCommands()
    if (version !== loadVersion) return
    const mine = all.filter((command) => command.plugin_id === pluginId)
    commands.value = mine
    // 重新加载会重置表单：schema 可能已经变了，留着旧值会送出非法参数。
    const next: Record<string, Record<string, unknown>> = {}
    for (const command of mine) next[command.command_id] = initialArguments(command)
    argumentsByCommand.value = next
  } catch (reason) {
    if (version === loadVersion) error.value = reason instanceof Error ? reason.message : t('命令加载失败', 'Failed to load commands')
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

function argsOf(commandId: string): Record<string, unknown> {
  return argumentsByCommand.value[commandId] ?? {}
}

function fieldValue(commandId: string, field: CommandField): string {
  const value = argsOf(commandId)[field.key]
  if (value === undefined || value === null) return ''
  return String(value)
}

function updateArgument(commandId: string, field: CommandField, raw: string) {
  const target = argumentsByCommand.value[commandId] ??= {}
  target[field.key] = coerceArgument(field, raw)
}

/**
 * when 条件求值。缺少上下文时禁用而不是硬跑 ——
 * 插件详情页没有编辑器选区，不冒充。
 */
function commandAvailable(command: PluginCommand): boolean {
  if (!command.enabled) return false
  return command.when.every((condition) => {
    if (condition === 'workspace.has_vault') return Boolean(workspaceStore.vaultId)
    if (condition === 'editor.has_note') return Boolean(editorStore.currentNoteId)
    if (condition === 'editor.has_selection') return false
    return false
  })
}

function missing(command: PluginCommand): CommandField[] {
  return missingRequiredFields(command, argsOf(command.command_id))
}

function canRun(command: PluginCommand): boolean {
  return commandAvailable(command) && missing(command).length === 0 && busy.value !== command.command_id
}

async function execute(command: PluginCommand) {
  const unfilled = missing(command)
  if (unfilled.length) {
    error.value = `请先填写必填参数：${unfilled.map((f) => f.title).join('、')}`
    return
  }
  busy.value = command.command_id
  error.value = ''
  notice.value = ''
  try {
    const result = await pluginService.executePluginCommand(
      command.command_id,
      cleanArguments(argsOf(command.command_id)),
      {
        vault_id: workspaceStore.hasVault ? workspaceStore.vaultId : null,
        note_id: editorStore.currentNoteId,
        file_path: editorStore.currentFilePath,
        selection: null,
      },
    )
    await applyCommandEffect(result.effect, {
      navigate: (path) => router.push(path),
      refresh: async (scope) => {
        if (scope === 'commands') await load()
        else if (scope === 'plugins') await pluginStore.loadPlugins()
        else if (scope === 'workspace') await workspaceStore.refreshFileTree()
        else emit('refresh-settings')
      },
      notify: (text) => { notice.value = text },
    })
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('命令执行失败', 'Command failed')
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <div class="command-panel">
    <div class="section-head">
      <div>
        <h3>{{ t('Plugin 命令', 'Plugin commands') }}</h3>
        <p>{{ t('执行该 Plugin 注册的受控 Command Contribution；参数表单由后端声明的 JSON Schema 生成。', 'Run controlled plugin commands using the parameter form defined by the plugin.') }}</p>
      </div>
      <button class="button-secondary" :disabled="loading" @click="load">
        <AppIcon :icon="Refresh" :size="15" />{{ t('刷新', 'Refresh') }}
      </button>
    </div>

    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="notice" class="notice-banner">{{ notice }}</div>

    <div v-if="commands.length" class="command-list">
      <article v-for="command in commands" :key="command.command_id" class="item-card command-card">
        <div class="command-head">
          <div>
            <strong>{{ command.title }}</strong>
            <p>{{ command.description || command.command_id }}</p>
          </div>
          <span
            class="badge"
            :class="{
              success: commandAvailable(command),
              warning: command.enabled && !commandAvailable(command),
            }"
          >{{ commandAvailable(command) ? t('可执行', 'Available') : command.enabled ? t('缺少上下文', 'Missing context') : t('不可用', 'Unavailable') }}</span>
        </div>

        <div v-if="commandFields(command).length" class="command-fields">
          <label v-for="field in commandFields(command)" :key="field.key" class="field">
            <span>
              {{ field.title }}
              <em v-if="field.required">{{ t('必填', 'Required') }}</em>
            </span>
            <select
              v-if="field.enum"
              class="select"
              :value="fieldValue(command.command_id, field)"
              @change="updateArgument(command.command_id, field, ($event.target as HTMLSelectElement).value)"
            >
              <option value="">{{ t('请选择', 'Select') }}</option>
              <option v-for="option in field.enum" :key="option" :value="option">{{ option }}</option>
            </select>
            <select
              v-else-if="field.type === 'boolean'"
              class="select"
              :value="fieldValue(command.command_id, field)"
              @change="updateArgument(command.command_id, field, ($event.target as HTMLSelectElement).value)"
            >
              <option value="false">{{ t('否', 'No') }}</option>
              <option value="true">{{ t('是', 'Yes') }}</option>
            </select>
            <input
              v-else
              class="input"
              :type="field.type === 'number' || field.type === 'integer' ? 'number' : 'text'"
              :required="field.required"
              :value="fieldValue(command.command_id, field)"
              @input="updateArgument(command.command_id, field, ($event.target as HTMLInputElement).value)"
            />
            <small v-if="field.description">{{ field.description }}</small>
          </label>
        </div>

        <p v-if="commandAvailable(command) && missing(command).length" class="missing-hint">
          待填写：{{ missing(command).map((f) => f.title).join('、') }}
        </p>

        <button
          class="button-primary command-run"
          :disabled="!canRun(command)"
          @click="execute(command)"
        >
          <AppIcon :icon="VideoPlay" :size="15" />
          {{ busy === command.command_id ? t('执行中…', 'Running…') : t('执行命令', 'Run command') }}
        </button>
      </article>
    </div>
    <div v-else-if="!loading" class="empty-state">
      <div><strong>{{ t('没有可用命令', 'No available commands') }}</strong><p>{{ t('启用 Plugin 后，已注册的命令会出现在这里。', 'Registered commands appear here after the Plugin is enabled.') }}</p></div>
    </div>
  </div>
</template>

<style scoped>
.command-panel { min-height: 220px; }
.section-head, .command-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-md); margin-bottom: var(--space-lg); }
.section-head p, .command-head p { margin-top: var(--space-xs); color: var(--color-text-tertiary); font-size: var(--font-size-sm); }
.section-head button, .command-run { display: inline-flex; align-items: center; gap: var(--space-xs); }
.command-list, .command-card { display: grid; gap: var(--space-sm); }
.command-card:hover { transform: none; }
.command-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: var(--space-md); }
.command-fields small { color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
.command-run { justify-self: end; }
.missing-hint { color: var(--color-warning); font-size: var(--font-size-sm); }
em { margin-left: var(--space-xs); color: var(--color-error); font-size: var(--font-size-xs); font-style: normal; }
@media (max-width: 800px) { .command-fields { grid-template-columns: 1fr; } }
</style>
