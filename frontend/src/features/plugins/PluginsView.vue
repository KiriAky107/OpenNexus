<script setup lang="ts">
import { Connection } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'
import PluginMcpPanel from './PluginMcpPanel.vue'
import { onMounted, ref } from 'vue'
import { usePluginStore } from '@/stores/plugin'

const pluginStore = usePluginStore()
const actionError = ref('')
onMounted(() => { void pluginStore.loadPlugins() })

async function install() { const path = prompt('请输入 Plugin Package 路径')?.trim(); if (!path) return; try { await pluginStore.installPlugin(path) } catch (error) { actionError.value = error instanceof Error ? error.message : '安装失败' } }
async function toggle(id: string, enabled: boolean) { try { enabled ? await pluginStore.disablePlugin(id) : await pluginStore.enablePlugin(id) } catch (error) { actionError.value = error instanceof Error ? error.message : '状态更新失败' } }
async function grant(id: string, permissions: string[]) { if (!confirm(`将授权：${permissions.join('、')}。是否继续？`)) return; try { await pluginStore.grantPermissions(id, permissions) } catch (error) { actionError.value = error instanceof Error ? error.message : '授权失败' } }
async function uninstall(id: string, name: string) { if (!confirm(`卸载“${name}”将移除其全部 Contribution，是否继续？`)) return; try { await pluginStore.uninstallPlugin(id) } catch (error) { actionError.value = error instanceof Error ? error.message : '卸载失败' } }
</script>

<template>
  <section class="feature-page">
    <header class="feature-header"><div><h1>Plugin 与 MCP</h1><p>管理插件生命周期、MCP Host、权限和受控 Contribution。</p></div><button class="button-primary" @click="install">安装 Plugin</button></header>
    <div v-if="pluginStore.error || actionError" class="error-banner">{{ pluginStore.error || actionError }}</div>
    <div v-if="pluginStore.selectedPlugin" class="panel">
      <div class="detail-head"><div><span class="badge" :class="{ success: pluginStore.selectedPlugin.status === 'ready', error: pluginStore.selectedPlugin.status === 'error', warning: pluginStore.selectedPlugin.status === 'permission_required' }">{{ pluginStore.selectedPlugin.status }}</span><h2>{{ pluginStore.selectedPlugin.icon }} {{ pluginStore.selectedPlugin.name }}</h2><p class="muted">v{{ pluginStore.selectedPlugin.version }} · {{ pluginStore.selectedPlugin.backend_type || 'none' }}/{{ pluginStore.selectedPlugin.transport || 'none' }}</p></div><div class="inline-actions"><button v-if="pluginStore.selectedPlugin.status === 'permission_required'" class="button-primary" @click="grant(pluginStore.selectedPlugin.plugin_id, pluginStore.selectedPlugin.permissions)">授权权限</button><button class="button-secondary" @click="toggle(pluginStore.selectedPlugin.plugin_id, pluginStore.selectedPlugin.enabled)">{{ pluginStore.selectedPlugin.enabled ? '停用' : '启用' }}</button><button class="button-danger" @click="uninstall(pluginStore.selectedPlugin.plugin_id, pluginStore.selectedPlugin.name)">卸载</button></div></div>
      <p class="description">{{ pluginStore.selectedPlugin.description }}</p>
      <div class="detail-grid"><div><h3>权限</h3><div class="tag-list"><span v-for="permission in pluginStore.selectedPlugin.permissions" :key="permission" class="badge warning">{{ permission }}</span></div></div><div><h3>Contribution</h3><div class="contribution-list"><div v-for="item in pluginStore.selectedPlugin.contributions" :key="item.id" class="item-card"><span class="badge info">{{ item.type }}</span><strong>{{ item.name }}</strong><p class="subtle">{{ item.description || item.id }}</p></div></div></div></div>
      <div v-if="pluginStore.selectedPlugin.last_error" class="error-banner last-error">{{ pluginStore.selectedPlugin.last_error }}</div>
      <div v-if="pluginStore.selectedPlugin.dependent_skills?.length" class="notice-banner last-error">依赖此插件的 Skill：{{ pluginStore.selectedPlugin.dependent_skills.join('、') }}</div>
      <PluginMcpPanel :plugin="pluginStore.selectedPlugin" />
    </div>
    <div v-else-if="!pluginStore.plugins.length" class="empty-state"><div><strong>{{ pluginStore.isLoading ? '正在加载…' : pluginStore.error ? '加载失败' : '尚未安装' }}</strong><button class="button-secondary" @click="pluginStore.loadPlugins">重新加载</button></div></div>
    <div v-else class="feature-grid"><article v-for="plugin in pluginStore.plugins" :key="plugin.plugin_id" class="item-card extension-card" @click="pluginStore.selectPlugin(plugin.plugin_id)"><div class="extension-title"><AppIcon :icon="Connection" :size="22" /><div><strong>{{ plugin.name }}</strong><p>v{{ plugin.version }}</p></div><span class="badge" :class="{ success: plugin.status === 'ready', error: plugin.status === 'error', warning: plugin.status === 'permission_required' }">{{ plugin.status }}</span></div><p class="muted">{{ plugin.description }}</p><p class="subtle">{{ plugin.permissions.length }} 项权限 · {{ plugin.contributions.length }} 项 Contribution</p></article></div>
  </section>
</template>

<style scoped>
.detail-head, .extension-title { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-md); }
.detail-head h2 { margin-top: var(--space-sm); }
.description { margin: var(--space-xl) 0; line-height: var(--line-height-relaxed); }
.detail-grid { display: grid; grid-template-columns: minmax(220px, .7fr) minmax(320px, 1.3fr); gap: var(--space-xl); }
.detail-grid h3 { margin-bottom: var(--space-sm); }
.contribution-list { display: grid; gap: var(--space-sm); }
.contribution-list .item-card { display: grid; gap: var(--space-xs); }
.last-error { margin: var(--space-xl) 0 0; }
.extension-card { cursor: pointer; }
.extension-card > p { margin-top: var(--space-md); }
.extension-title { align-items: center; }
.extension-title .icon { font-size: 28px; }
.extension-title p { color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
@media (max-width: 800px) { .detail-grid { grid-template-columns: 1fr; } }
</style>
