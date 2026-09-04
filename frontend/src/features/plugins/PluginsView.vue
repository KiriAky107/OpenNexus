<script setup lang="ts">
import { Connection } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'
import PluginMcpPanel from './PluginMcpPanel.vue'
import PluginSettingsPanel from './PluginSettingsPanel.vue'
import { computed, onMounted, ref, watch } from 'vue'
import { usePluginStore } from '@/stores/plugin'
import * as pluginService from '@/services/pluginService'
import type { PluginCommand, PluginCommandEffect } from '@/contracts'

const pluginStore = usePluginStore()
const actionError = ref('')
const activeTab = ref<'info' | 'settings' | 'commands'>('info')
const pluginCommands = ref<PluginCommand[]>([])
const commandOutput = ref<Record<string, string>>({})
const isExecutingCommand = ref<string | null>(null)

onMounted(() => { void pluginStore.loadPlugins() })

watch(() => pluginStore.selectedPluginId, async (pluginId) => {
  if (pluginId) {
    activeTab.value = 'info'
    pluginCommands.value = []
    commandOutput.value = {}
    try {
      const allCommands = await pluginService.listPluginCommands()
      pluginCommands.value = allCommands.filter((c) => c.plugin_id === pluginId)
    } catch { /* 命令加载失败时忽略 */ }
  }
})

async function install() {
  const path = prompt('请输入 Plugin Package 路径')?.trim()
  if (!path) return
  try { await pluginStore.installPlugin(path) }
  catch (error) { actionError.value = error instanceof Error ? error.message : '安装失败' }
}

async function toggle(id: string, enabled: boolean) {
  try {
    enabled ? await pluginStore.disablePlugin(id) : await pluginStore.enablePlugin(id)
  } catch (error) { actionError.value = error instanceof Error ? error.message : '状态更新失败' }
}

async function grant(id: string, permissions: string[]) {
  if (!confirm(`将授权：${permissions.join('、')}。是否继续？`)) return
  try { await pluginStore.grantPermissions(id, permissions) }
  catch (error) { actionError.value = error instanceof Error ? error.message : '授权失败' }
}

async function uninstall(id: string, name: string) {
  if (!confirm(`卸载"${name}"将移除其全部 Contribution，是否继续？`)) return
  try { await pluginStore.uninstallPlugin(id) }
  catch (error) { actionError.value = error instanceof Error ? error.message : '卸载失败' }
}

async function runCommand(command: PluginCommand) {
  isExecutingCommand.value = command.command_id
  commandOutput.value[command.command_id] = ''
  try {
    // Plugin 详情页没有笔记/选区上下文，按契约传空上下文。
    const result = await pluginService.executePluginCommand(command.command_id, {}, {})
    commandOutput.value[command.command_id] = describeEffect(result.effect)
  } catch (error) {
    commandOutput.value[command.command_id] = error instanceof Error ? error.message : '执行失败'
  } finally {
    isExecutingCommand.value = null
  }
}

/** 效果白名单：只渲染契约允许的类型，未知类型统一按“已完成”处理。 */
function describeEffect(effect: PluginCommandEffect): string {
  switch (effect.type) {
    case 'notification':
      return effect.payload.message
    case 'navigate':
      return `命令请求跳转到「${effect.payload.route}」`
    case 'refresh':
      return `命令请求刷新「${effect.payload.scope}」`
    case 'job':
      return `已创建后台任务：${effect.payload.job_id}`
    default:
      return '命令执行成功'
  }
}

const hasSettingsContribution = computed(() =>
  pluginStore.selectedPlugin?.contributions.some((c) => c.type === 'settings_section') ?? false
)

const hasCommandContribution = computed(() =>
  pluginStore.selectedPlugin?.contributions.some((c) => c.type === 'command') ?? false
)
</script>

<template>
  <section class="feature-page">
    <header class="feature-header">
      <div><h1>Plugin 与 MCP</h1><p>管理插件生命周期、MCP Host、权限和受控 Contribution。</p></div>
      <button class="button-primary" @click="install">安装 Plugin</button>
    </header>

    <div v-if="pluginStore.error || actionError" class="error-banner">
      {{ pluginStore.error || actionError }}
    </div>

    <div v-if="pluginStore.selectedPlugin" class="plugin-detail">
      <div class="panel detail-panel">
        <div class="detail-head">
          <div>
            <span class="badge" :class="{
              success: pluginStore.selectedPlugin.status === 'ready',
              error: pluginStore.selectedPlugin.status === 'error',
              warning: pluginStore.selectedPlugin.status === 'permission_required',
              info: pluginStore.selectedPlugin.status === 'starting',
            }">{{ pluginStore.selectedPlugin.status }}</span>
            <h2>{{ pluginStore.selectedPlugin.icon }} {{ pluginStore.selectedPlugin.name }}</h2>
            <p class="muted">
              v{{ pluginStore.selectedPlugin.version }}
              · {{ pluginStore.selectedPlugin.backend_type || 'none' }}/{{ pluginStore.selectedPlugin.transport || 'none' }}
              · {{ pluginStore.selectedPlugin.author || '未知作者' }}
            </p>
          </div>
          <div class="inline-actions">
            <button
              v-if="pluginStore.selectedPlugin.status === 'permission_required'"
              class="button-primary"
              @click="grant(pluginStore.selectedPlugin.plugin_id, pluginStore.selectedPlugin.permissions)"
            >授权权限</button>
            <button
              class="button-secondary"
              @click="toggle(pluginStore.selectedPlugin.plugin_id, pluginStore.selectedPlugin.enabled)"
            >{{ pluginStore.selectedPlugin.enabled ? '停用' : '启用' }}</button>
            <button
              class="button-danger"
              @click="uninstall(pluginStore.selectedPlugin.plugin_id, pluginStore.selectedPlugin.name)"
            >卸载</button>
          </div>
        </div>

        <p class="description">{{ pluginStore.selectedPlugin.description }}</p>

        <div class="detail-tabs">
          <button
            class="tab-btn"
            :class="{ active: activeTab === 'info' }"
            @click="activeTab = 'info'"
          >概览</button>
          <button
            v-if="hasCommandContribution"
            class="tab-btn"
            :class="{ active: activeTab === 'commands' }"
            @click="activeTab = 'commands'"
          >命令 ({{ pluginCommands.length }})</button>
          <button
            v-if="hasSettingsContribution || pluginCommands.some(c => c.enabled)"
            class="tab-btn"
            :class="{ active: activeTab === 'settings' }"
            @click="activeTab = 'settings'"
          >设置</button>
        </div>

        <div v-if="activeTab === 'info'" class="tab-content">
          <div class="detail-grid">
            <div>
              <h3>权限</h3>
              <div class="tag-list">
                <span v-for="permission in pluginStore.selectedPlugin.permissions" :key="permission" class="badge warning">
                  {{ permission }}
                </span>
              </div>
            </div>
            <div>
              <h3>Contribution</h3>
              <div class="contribution-list">
                <div
                  v-for="item in pluginStore.selectedPlugin.contributions"
                  :key="item.id"
                  class="item-card"
                >
                  <span class="badge info">{{ item.type }}</span>
                  <strong>{{ item.name }}</strong>
                  <p class="subtle">{{ item.description || item.id }}</p>
                </div>
              </div>
            </div>
          </div>
          <div v-if="pluginStore.selectedPlugin.last_error" class="error-banner last-error">
            {{ pluginStore.selectedPlugin.last_error }}
          </div>
          <div v-if="pluginStore.selectedPlugin.dependent_skills?.length" class="notice-banner">
            依赖此插件的 Skill：{{ pluginStore.selectedPlugin.dependent_skills.join('、') }}
          </div>
          <PluginMcpPanel :plugin="pluginStore.selectedPlugin" />
        </div>

        <div v-else-if="activeTab === 'commands'" class="tab-content">
          <div v-if="pluginCommands.length === 0" class="empty-hint">
            <p>此插件暂无可执行命令。</p>
          </div>
          <div v-else class="command-list">
            <div v-for="cmd in pluginCommands" :key="cmd.command_id" class="command-item">
              <div class="command-info">
                <strong>{{ cmd.title }}</strong>
                <p class="subtle">{{ cmd.description }}</p>
                <div class="command-meta">
                  <code>{{ cmd.command_id }}</code>
                  <span class="locations">
                    挂载于: {{ cmd.locations.join(', ') }}
                  </span>
                </div>
              </div>
              <div class="command-action">
                <button
                  class="button-secondary"
                  :disabled="!cmd.enabled || isExecutingCommand === cmd.command_id"
                  @click="runCommand(cmd)"
                >
                  {{ isExecutingCommand === cmd.command_id ? '执行中…' : '运行' }}
                </button>
              </div>
              <div v-if="commandOutput[cmd.command_id]" class="command-output">
                {{ commandOutput[cmd.command_id] }}
              </div>
            </div>
          </div>
        </div>

        <div v-else-if="activeTab === 'settings'" class="tab-content">
          <PluginSettingsPanel :plugin-id="pluginStore.selectedPlugin.plugin_id" />
        </div>
      </div>
    </div>

    <div v-else-if="!pluginStore.plugins.length" class="empty-state">
      <div>
        <strong>{{ pluginStore.isLoading ? '正在加载…' : pluginStore.error ? '加载失败' : '尚未安装' }}</strong>
        <button class="button-secondary" @click="pluginStore.loadPlugins">重新加载</button>
      </div>
    </div>

    <div v-else class="feature-grid">
      <article
        v-for="plugin in pluginStore.plugins"
        :key="plugin.plugin_id"
        class="item-card extension-card"
        @click="pluginStore.selectPlugin(plugin.plugin_id)"
      >
        <div class="extension-title">
          <AppIcon :icon="Connection" :size="22" />
          <div>
            <strong>{{ plugin.name }}</strong>
            <p>v{{ plugin.version }}</p>
          </div>
          <span
            class="badge"
            :class="{
              success: plugin.status === 'ready',
              error: plugin.status === 'error',
              warning: plugin.status === 'permission_required',
            }"
          >{{ plugin.status }}</span>
        </div>
        <p class="muted">{{ plugin.description }}</p>
        <p class="subtle">
          {{ plugin.permissions.length }} 项权限 · {{ plugin.contributions.length }} 项 Contribution
        </p>
      </article>
    </div>
  </section>
</template>

<style scoped>
.plugin-detail { display: grid; gap: var(--space-lg); }

.detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-md);
}
.detail-head h2 { margin-top: var(--space-sm); }
.detail-head .muted {
  color: var(--color-text-tertiary);
  font-size: var(--font-size-sm);
  margin-top: 4px;
}

.description {
  margin: var(--space-xl) 0;
  line-height: var(--line-height-relaxed);
}

.detail-tabs {
  display: flex;
  gap: var(--space-sm);
  border-bottom: 1px solid var(--color-border-default);
  margin-bottom: var(--space-lg);
}

.tab-btn {
  padding: var(--space-sm) var(--space-md);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: var(--font-size-md);
  margin-bottom: -1px;
  transition: all var(--motion-fast);
}
.tab-btn:hover { color: var(--color-text-primary); }
.tab-btn.active {
  color: var(--color-accent-primary);
  border-bottom-color: var(--color-accent-primary);
  font-weight: 500;
}

.tab-content { min-height: 200px; }

.detail-grid {
  display: grid;
  grid-template-columns: minmax(220px, .7fr) minmax(320px, 1.3fr);
  gap: var(--space-xl);
}
.detail-grid h3 { margin-bottom: var(--space-sm); }

.contribution-list { display: grid; gap: var(--space-sm); }
.contribution-list .item-card { display: grid; gap: var(--space-xs); }

.tag-list { display: flex; flex-wrap: wrap; gap: 6px; }

.last-error { margin: var(--space-xl) 0 0; }

.notice-banner {
  margin-top: var(--space-xl);
  padding: var(--space-sm) var(--space-md);
  border-radius: var(--radius-md);
  background: var(--color-info-soft);
  color: var(--color-info);
  font-size: var(--font-size-sm);
}

.extension-card { cursor: pointer; }
.extension-card > p { margin-top: var(--space-md); }
.extension-title { display: flex; align-items: center; gap: var(--space-sm); }
.extension-title div { flex: 1; }
.extension-title p { color: var(--color-text-tertiary); font-size: var(--font-size-xs); }

.command-list { display: grid; gap: var(--space-sm); }
.command-item {
  padding: var(--space-md);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: var(--color-surface-primary);
  display: grid;
  grid-template-columns: 1fr auto;
  gap: var(--space-sm) var(--space-md);
  align-items: start;
}
.command-info strong { display: block; margin-bottom: 2px; }
.command-info .subtle {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
  margin-bottom: var(--space-xs);
}
.command-meta {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
}
.command-meta code {
  padding: 1px 6px;
  background: var(--color-background-secondary);
  border-radius: var(--radius-sm);
  font-family: var(--font-ui-mono);
}
.command-output {
  grid-column: 1 / -1;
  padding: var(--space-sm) var(--space-md);
  background: var(--color-background-secondary);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
  white-space: pre-wrap;
}

.empty-hint {
  padding: var(--space-2xl);
  text-align: center;
  color: var(--color-text-tertiary);
  font-size: var(--font-size-sm);
}

@media (max-width: 800px) {
  .detail-grid { grid-template-columns: 1fr; }
  .command-item { grid-template-columns: 1fr; }
}
</style>
