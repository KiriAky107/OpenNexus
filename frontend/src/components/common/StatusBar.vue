<script setup lang="ts">
import { computed } from 'vue'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { useProviderStore } from '@/stores/provider'
import { useAgentStore } from '@/stores/agent'
import { useRoute } from 'vue-router'

const editorStore = useEditorStore()
const settingsStore = useSettingsStore()
const providerStore = useProviderStore()
const agentStore = useAgentStore()
const route = useRoute()

const saveStatusText = computed(() => {
  const map: Record<string, string> = {
    idle: '',
    dirty: '未保存',
    saving: '保存中...',
    saved: '已保存',
    save_failed: '保存失败',
    external_changed: '外部已更新',
    conflict: '存在冲突',
  }
  return map[editorStore.saveStatus] || ''
})

const saveStatusColor = computed(() => {
  const map: Record<string, string> = {
    dirty: 'var(--color-accent-primary)',
    saving: 'var(--color-info)',
    saved: 'var(--color-success)',
    save_failed: 'var(--color-error)',
    external_changed: 'var(--color-warning)',
    conflict: 'var(--color-error)',
  }
  return map[editorStore.saveStatus] || 'var(--color-text-tertiary)'
})

const indexStatusText = computed(() => {
  const s = settingsStore.indexStatus.status
  return s === 'idle' ? '索引就绪' : s === 'indexing' ? `索引中 (${settingsStore.indexStatus.pending_jobs})` : '索引错误'
})

const aiCoreStatusText = computed(() => {
  const map: Record<string, string> = {
    starting: 'AI Core 启动中',
    running: 'AI Core 运行中',
    stopped: 'AI Core 已停止',
    error: 'AI Core 错误',
  }
  return map[settingsStore.aiCoreStatus] || ''
})

const aiCoreColor = computed(() => {
  const map: Record<string, string> = {
    starting: 'var(--color-warning)',
    running: 'var(--color-success)',
    stopped: 'var(--color-text-tertiary)',
    error: 'var(--color-error)',
  }
  return map[settingsStore.aiCoreStatus] || ''
})

const defaultProvider = computed(() => providerStore.defaultProvider)

const showEditorInfo = computed(() => route.name === 'workspace')
</script>

<template>
  <footer class="statusbar">
    <div class="statusbar-left">
      <span v-if="showEditorInfo && saveStatusText" class="status-item" :style="{ color: saveStatusColor }">
        <span class="status-dot" :style="{ background: saveStatusColor }" />
        {{ saveStatusText }}
      </span>
      <span class="status-item" :title="indexStatusText">
        <span class="status-dot" style="background: var(--color-success)" />
        索引就绪
      </span>
      <span class="status-item" :style="{ color: aiCoreColor }" @click>
        <span class="status-dot" :style="{ background: aiCoreColor }" />
        {{ aiCoreStatusText }}
      </span>
      <span v-if="agentStore.isRunning" class="status-item agent-status">
        <span class="spinner" />
        Agent 运行中
      </span>
    </div>
    <div class="statusbar-right">
      <span v-if="defaultProvider" class="status-item provider-info">
        {{ defaultProvider.name }} · {{ defaultProvider.default_model }}
      </span>
      <span v-if="showEditorInfo" class="status-item">
        {{ editorStore.lineCount }} 行
      </span>
      <span v-if="showEditorInfo" class="status-item">
        {{ editorStore.wordCount }} 字
      </span>
    </div>
  </footer>
</template>

<style scoped>
.statusbar {
  height: var(--statusbar-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--space-md);
  background: var(--color-background-secondary);
  border-top: 1px solid var(--color-border-subtle);
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
  flex-shrink: 0;
  user-select: none;
}

.statusbar-left,
.statusbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-md);
}

.status-item {
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  cursor: default;

  &:hover {
    color: var(--color-text-primary);
  }
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.agent-status {
  color: var(--color-accent-primary);
}

.spinner {
  width: 10px;
  height: 10px;
  border: 2px solid var(--color-accent-soft);
  border-top-color: var(--color-accent-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.provider-info {
  color: var(--color-text-tertiary);
}
</style>
