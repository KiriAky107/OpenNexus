<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AppDialog from '@/components/common/AppDialog.vue'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import { usePluginStore } from '@/stores/plugin'
import { listPluginCommands, executePluginCommand } from '@/services/pluginService'
import { applyCommandEffect, commandFields, initialArguments, coerceArgument, cleanArguments, missingRequiredFields } from '@/services/pluginCommandForm'
import type { PluginCommand, PluginCommandContext, PluginCommandLocation } from '@/contracts'
import { t } from '@/i18n'

const editor = useEditorStore(), workspace = useWorkspaceStore(), plugins = usePluginStore(), router = useRouter()
const open = ref(false), busy = ref(false), error = ref(''), notice = ref('')
const commands = ref<PluginCommand[]>([]), selected = ref<PluginCommand | null>(null)
const args = ref<Record<string, unknown>>({})
const snapshot = ref<PluginCommandContext>({ vault_id: null, note_id: null, file_path: null, selection: null })
let revision = 0
function capture() {
  const input = document.activeElement
  const selection = input instanceof HTMLTextAreaElement
    ? input.value.slice(input.selectionStart, input.selectionEnd)
    : window.getSelection()?.toString() || ''
  snapshot.value = { vault_id: workspace.hasVault ? workspace.vaultId : null, note_id: editor.currentNoteId, file_path: editor.currentFilePath, selection: selection || null }
}
function available(command: PluginCommand) {
  const context = snapshot.value
  return command.enabled && command.when.every(condition => ({
    'workspace.has_vault': Boolean(context.vault_id), 'editor.has_note': Boolean(context.note_id), 'editor.has_selection': Boolean(context.selection),
  })[condition])
}
async function show(location: PluginCommandLocation) {
  const version = ++revision
  open.value = true; error.value = ''; selected.value = null; commands.value = []
  try {
    const result = await listPluginCommands(location)
    if (version === revision) commands.value = result.filter(available)
  } catch (reason) { if (version === revision) error.value = String(reason) }
}
function contextMenu(event: MouseEvent) {
  if (!(event.target instanceof Element) || !event.target.closest('.ProseMirror, .source')) return
  event.preventDefault(); capture(); void show('context_menu')
}
function choose(command: PluginCommand) { selected.value = command; args.value = initialArguments(command) }
function close() { if (!busy.value) { open.value = false; revision++ } }
watch(() => editor.currentFilePath, () => { open.value = false; revision++ })
watch(() => plugins.plugins, () => { if (!busy.value) close() }, { deep: true })
const fields = computed(() => selected.value ? commandFields(selected.value) : [])
async function run() {
  const command = selected.value
  if (!command || busy.value || !available(command)) return
  if (snapshot.value.file_path !== editor.currentFilePath) { close(); return }
  if (missingRequiredFields(command, args.value).length) { error.value = t('请填写必填参数', 'Complete required fields'); return }
  busy.value = true; error.value = ''
  try {
    // Runtime rechecks enabled state, schema, when conditions and permissions.
    const result = await executePluginCommand(command.command_id, cleanArguments(args.value), { ...snapshot.value })
    await applyCommandEffect(result.effect, {
      navigate: path => router.push(path),
      refresh: async scope => {
        if (scope === 'workspace') await workspace.refreshFileTree()
        else if (scope === 'plugins') await plugins.loadPlugins()
        else if (scope === 'commands') commands.value = (await listPluginCommands()).filter(available)
        else { plugins.selectPlugin(command.plugin_id); await router.push('/extensions/plugins') }
      },
      notify: value => { notice.value = value },
    })
    open.value = false
  } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { busy.value = false }
}
</script>

<template>
  <div class="workspace-plugin-host" @contextmenu="contextMenu">
    <div class="workspace-plugin-toolbar" role="toolbar" :aria-label="t('扩展工具栏', 'Extension toolbar')">
      <button class="button-secondary" @pointerdown.prevent="capture" @click="event => { if (!event.detail) capture(); show('toolbar') }">{{ t('扩展命令', 'Extension commands') }}</button>
      <span v-if="notice" role="status">{{ notice }}</span>
    </div>
    <slot />
    <AppDialog v-if="open" :label="t('扩展命令', 'Extension commands')" :dismissible="!busy" @close="close">
      <section class="modal">
        <div class="section-head"><h2>{{ t('扩展命令', 'Extension commands') }}</h2><button class="button-secondary" :disabled="busy" @click="close">{{ t('关闭', 'Close') }}</button></div>
        <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
        <div class="workspace-command-list">
          <button v-for="command in commands" :key="command.command_id" class="button-secondary" :disabled="busy" :aria-pressed="selected?.command_id === command.command_id" @click="choose(command)">{{ command.title }}</button>
          <p v-if="!commands.length">{{ t('当前上下文没有可用的扩展命令。', 'No extension commands are available in this context.') }}</p>
        </div>
        <form v-if="selected" @submit.prevent="run">
          <p>{{ selected.description }}</p>
          <label v-for="field in fields" :key="field.key" class="form-field">
            <span>{{ field.title }}{{ field.required ? ' *' : '' }}</span>
            <select v-if="field.enum || field.type === 'boolean'" class="select" :value="String(args[field.key] ?? '')" @change="args[field.key] = coerceArgument(field, ($event.target as HTMLSelectElement).value)">
              <option value="">{{ t('请选择', 'Select') }}</option><option v-for="value in field.enum || ['false', 'true']" :key="value" :value="value">{{ value }}</option>
            </select>
            <input v-else class="input" :required="field.required" :type="['number','integer'].includes(field.type) ? 'number' : 'text'" :value="String(args[field.key] ?? '')" @input="args[field.key] = coerceArgument(field, ($event.target as HTMLInputElement).value)" />
            <small>{{ field.description }}</small>
          </label>
          <button class="button-primary" :disabled="busy">{{ t('执行', 'Run') }}</button>
        </form>
      </section>
    </AppDialog>
  </div>
</template>

<style scoped>
.workspace-plugin-host { display: contents; }
.workspace-plugin-toolbar { display: flex; gap: var(--space-sm); align-items: center; padding: var(--space-xs) var(--space-md); background: var(--color-background-secondary); border-bottom: 1px solid var(--color-border-default); }
.workspace-plugin-toolbar span { overflow-wrap: anywhere; font-size: var(--font-size-sm); }
.workspace-command-list, form { display: grid; gap: var(--space-sm); margin-block: var(--space-md); }
.section-head { display: flex; align-items: center; justify-content: space-between; gap: var(--space-sm); }
</style>
