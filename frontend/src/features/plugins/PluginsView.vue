<script setup lang="ts">
import ExtensionRestoreNotice from '@/components/common/ExtensionRestoreNotice.vue'
import ActionDialog from '@/components/common/ActionDialog.vue'
import { useActionDialog } from '@/composables/useActionDialog'
const { actionDialog, resolveAction, askConfirm } = useActionDialog()
import { Connection } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'
import ExtensionInstallDialog from '@/components/common/ExtensionInstallDialog.vue'
import PluginMcpPanel from './PluginMcpPanel.vue'
import PluginCommandPanel from './PluginCommandPanel.vue'
import PluginSettingsPanel from './PluginSettingsPanel.vue'
import { computed, onMounted, ref, watch } from 'vue'
import { usePluginStore } from '@/stores/plugin'
import * as pluginService from '@/services/pluginService'
import type { PluginCommand } from '@/contracts'
import { t } from '@/i18n'

const pluginStore = usePluginStore()
const actionError = ref('')
const showInstall = ref(false)
const activeTab = ref<'info' | 'settings' | 'commands'>('info')
const pluginCommands = ref<PluginCommand[]>([])

onMounted(() => { void pluginStore.loadPlugins() })

watch(() => pluginStore.selectedPluginId, async (pluginId) => {
  if (pluginId) {
    activeTab.value = 'info'
    pluginCommands.value = []
    try {
      // 只为了标签上的命令数；执行逻辑在 PluginCommandPanel 里。
      const allCommands = await pluginService.listPluginCommands()
      pluginCommands.value = allCommands.filter((c) => c.plugin_id === pluginId)
    } catch { /* 命令加载失败时忽略 */ }
  }
})


async function toggle(id: string, enabled: boolean) {
  try {
    enabled ? await pluginStore.disablePlugin(id) : await pluginStore.enablePlugin(id)
  } catch (error) { actionError.value = error instanceof Error ? error.message : t('状态更新失败', 'Status update failed') }
}

async function grant(id: string, permissions: string[]) {
  if (!(await askConfirm(`${t('将授权：', 'Grant permissions: ')}${permissions.join(', ')}。${t('是否继续？', 'Continue?')}`))) return
  try { await pluginStore.grantPermissions(id, permissions) }
  catch (error) { actionError.value = error instanceof Error ? error.message : t('授权失败', 'Authorization failed') }
}

async function uninstall(id: string, name: string) {
  if (!(await askConfirm(t(`卸载「${name}」将移除其全部 Contribution，是否继续？`, `Uninstalling “${name}” removes all its contributions. Continue?`)))) return
  try { await pluginStore.uninstallPlugin(id) }
  catch (error) { actionError.value = error instanceof Error ? error.message : t('卸载失败', 'Uninstall failed') }
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
    <ExtensionRestoreNotice kind="plugin" />
    <ActionDialog v-if="actionDialog" v-bind="actionDialog" @resolve="resolveAction" />
    <ExtensionInstallDialog v-if="showInstall" kind="Plugin" :install="pluginStore.installPlugin" @close="showInstall = false" @installed="showInstall = false; actionError = ''" />
    <header class="feature-header">
      <div><h1>{{ t('Plugin 与 MCP', 'Plugins and MCP') }}</h1><p>{{ t('管理插件生命周期、MCP Host、权限和受控 Contribution。', 'Manage plugin lifecycles, MCP hosts, permissions, and controlled contributions.') }}</p></div>
      <button class="button-primary" @click="showInstall = true">{{ t('安装 Plugin', 'Install Plugin') }}</button>
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
            >{{ t('授权权限', 'Grant permissions') }}</button>
            <button
              class="button-secondary"
              @click="toggle(pluginStore.selectedPlugin.plugin_id, pluginStore.selectedPlugin.enabled)"
            >{{ pluginStore.selectedPlugin.enabled ? t('停用', 'Disable') : t('启用', 'Enable') }}</button>
            <button
              class="button-danger"
              @click="uninstall(pluginStore.selectedPlugin.plugin_id, pluginStore.selectedPlugin.name)"
            >{{ t('卸载', 'Uninstall') }}</button>
          </div>
        </div>

        <p class="description">{{ pluginStore.selectedPlugin.description }}</p>

        <div class="detail-tabs">
          <button
            class="tab-btn"
            :class="{ active: activeTab === 'info' }"
            @click="activeTab = 'info'"
          >{{ t('概览', 'Overview') }}</button>
          <button
            v-if="hasCommandContribution"
            class="tab-btn"
            :class="{ active: activeTab === 'commands' }"
            @click="activeTab = 'commands'"
          >{{ t('命令', 'Commands') }} ({{ pluginCommands.length }})</button>
          <button
            v-if="hasSettingsContribution || pluginCommands.some(c => c.enabled)"
            class="tab-btn"
            :class="{ active: activeTab === 'settings' }"
            @click="activeTab = 'settings'"
          >{{ t('设置', 'Settings') }}</button>
        </div>

        <div v-if="activeTab === 'info'" class="tab-content">
          <div class="detail-grid">
            <div>
              <h3>{{ t('权限', 'Permissions') }}</h3>
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
            {{ t('依赖此插件的 Skill：', 'Skills that depend on this plugin: ') }}{{ pluginStore.selectedPlugin.dependent_skills.join(', ') }}
          </div>
          <PluginMcpPanel :plugin="pluginStore.selectedPlugin" />
        </div>

        <div v-else-if="activeTab === 'commands'" class="tab-content">
          <PluginCommandPanel :plugin="pluginStore.selectedPlugin" />
        </div>

        <div v-else-if="activeTab === 'settings'" class="tab-content">
          <PluginSettingsPanel :plugin-id="pluginStore.selectedPlugin.plugin_id" />
        </div>
      </div>
    </div>

    <div v-else-if="!pluginStore.plugins.length" class="empty-state">
      <div>
        <strong>{{ pluginStore.isLoading ? t('正在加载…', 'Loading…') : pluginStore.error ? t('加载失败', 'Load failed') : t('尚未安装', 'No plugins installed') }}</strong>
        <button class="button-secondary" @click="pluginStore.loadPlugins">{{ t('重新加载', 'Reload') }}</button>
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
          {{ plugin.permissions.length }} {{ t('项权限', 'permissions') }} · {{ plugin.contributions.length }} {{ t('项 Contribution', 'contributions') }}
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

.empty-hint {
  padding: var(--space-2xl);
  text-align: center;
  color: var(--color-text-tertiary);
  font-size: var(--font-size-sm);
}

@media (max-width: 800px) {
  .detail-grid { grid-template-columns: 1fr; }
}
</style>
