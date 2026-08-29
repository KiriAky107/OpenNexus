import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Plugin } from '@/contracts'
import { mockPlugins } from '@/services/pluginService'

export const usePluginStore = defineStore('plugin', () => {
  const plugins = ref<Plugin[]>(mockPlugins)
  const selectedPluginId = ref<string | null>(null)
  const isLoading = ref(false)

  const selectedPlugin = computed(() =>
    plugins.value.find((p) => p.plugin_id === selectedPluginId.value) || null
  )

  const enabledPlugins = computed(() => plugins.value.filter((p) => p.enabled))
  const readyPlugins = computed(() => plugins.value.filter((p) => p.status === 'ready'))
  const errorPlugins = computed(() => plugins.value.filter((p) => p.status === 'error'))

  async function loadPlugins() {
    isLoading.value = true
    try {
      const { listPlugins } = await import('@/services/pluginService')
      plugins.value = await listPlugins()
    } finally {
      isLoading.value = false
    }
  }

  function selectPlugin(pluginId: string | null) {
    selectedPluginId.value = pluginId
  }

  async function enablePlugin(pluginId: string) {
    const plugin = plugins.value.find((p) => p.plugin_id === pluginId)
    if (plugin) {
      plugin.enabled = true
      plugin.status = 'ready'
    }
  }

  async function disablePlugin(pluginId: string) {
    const plugin = plugins.value.find((p) => p.plugin_id === pluginId)
    if (plugin) {
      plugin.enabled = false
      plugin.status = 'disabled'
    }
  }

  async function uninstallPlugin(pluginId: string) {
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
    loadPlugins,
    selectPlugin,
    enablePlugin,
    disablePlugin,
    uninstallPlugin,
  }
})
