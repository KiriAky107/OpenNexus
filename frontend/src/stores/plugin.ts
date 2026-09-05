import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Plugin } from '@/contracts'
import * as pluginService from '@/services/pluginService'
import { t } from '@/i18n'

export const usePluginStore = defineStore('plugin', () => {
  const plugins = ref<Plugin[]>([])
  const selectedPluginId = ref<string | null>(null)
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  const selectedPlugin = computed(() =>
    plugins.value.find((p) => p.plugin_id === selectedPluginId.value) || null
  )

  const enabledPlugins = computed(() => plugins.value.filter((p) => p.enabled))
  const readyPlugins = computed(() => plugins.value.filter((p) => p.status === 'ready'))
  const errorPlugins = computed(() => plugins.value.filter((p) => p.status === 'error'))

  async function loadPlugins() {
    isLoading.value = true
    try {
      plugins.value = await pluginService.listPlugins()
      error.value = null
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : t('Plugin 加载失败', 'Failed to load Plugins')
    } finally {
      isLoading.value = false
    }
  }

  function selectPlugin(pluginId: string | null) {
    selectedPluginId.value = pluginId
  }

  async function installPlugin(packagePath: string) {
    const installed = await pluginService.installPlugin(packagePath)
    const index = plugins.value.findIndex((plugin) => plugin.plugin_id === installed.plugin_id)
    if (index >= 0) plugins.value[index] = installed
    else plugins.value.unshift(installed)
    selectedPluginId.value = installed.plugin_id
  }

  async function grantPermissions(pluginId: string, permissions: string[]) {
    const updated = await pluginService.grantPluginPermissions(pluginId, permissions)
    const index = plugins.value.findIndex((plugin) => plugin.plugin_id === pluginId)
    if (index >= 0) plugins.value[index] = updated
  }

  async function enablePlugin(pluginId: string) {
    const updated = await pluginService.enablePlugin(pluginId)
    const index = plugins.value.findIndex((plugin) => plugin.plugin_id === pluginId)
    if (index >= 0) plugins.value[index] = updated
  }

  async function disablePlugin(pluginId: string) {
    const updated = await pluginService.disablePlugin(pluginId)
    const index = plugins.value.findIndex((plugin) => plugin.plugin_id === pluginId)
    if (index >= 0) plugins.value[index] = updated
  }

  async function uninstallPlugin(pluginId: string) {
    await pluginService.uninstallPlugin(pluginId)
    const idx = plugins.value.findIndex((p) => p.plugin_id === pluginId)
    if (idx > -1) plugins.value.splice(idx, 1)
    if (selectedPluginId.value === pluginId) selectedPluginId.value = null
  }

  return {
    plugins,
    selectedPluginId,
    selectedPlugin,
    enabledPlugins,
    readyPlugins,
    errorPlugins,
    isLoading,
    error,
    loadPlugins,
    selectPlugin,
    installPlugin,
    grantPermissions,
    enablePlugin,
    disablePlugin,
    uninstallPlugin,
  }
})
