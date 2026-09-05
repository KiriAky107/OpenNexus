<script setup lang="ts">
import { computed } from 'vue'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { useProviderStore } from '@/stores/provider'
import { useAgentStore } from '@/stores/agent'
import { useRoute } from 'vue-router'
import { t } from '@/i18n'

const editorStore = useEditorStore()
const settingsStore = useSettingsStore()
const providerStore = useProviderStore()
const agentStore = useAgentStore()
const route = useRoute()

const saveStatusText = computed(() => {
  const map: Record<string, string> = {
    idle: '',
    dirty: t('未保存', 'Unsaved'),
    saving: t('保存中...', 'Saving...'),
    saved: t('已保存', 'Saved'),
    save_failed: t('保存失败', 'Save failed'),
    external_changed: t('外部已更新', 'Changed externally'),
    conflict: t('存在冲突', 'Conflict'),
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
  if (s === 'idle' && settingsStore.indexStatus.vector_refresh_required) return t('全文可用 · 向量待重建', 'Full text ready · vectors need rebuilding')
  return s === 'unknown' ? t('索引状态未获取', 'Index status unavailable') : s === 'idle' ? t('索引就绪', 'Index ready') : s === 'indexing' ? t('后台计算索引', 'Indexing in background') : t('索引错误', 'Index error')
})

const aiCoreStatusText = computed(() => {
  const map: Record<string, string> = {
    unknown: t('AI Core 状态未获取', 'AI Core status unavailable'),
    starting: t('AI Core 启动中', 'AI Core starting'),
    running: t('AI Core 运行中', 'AI Core running'),
    stopped: t('AI Core 已停止', 'AI Core stopped'),
    error: t('AI Core 错误', 'AI Core error'),
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
        <span class="status-dot" :style="{ background: settingsStore.indexStatus.status === 'error' ? 'var(--color-error)' : settingsStore.indexStatus.status === 'indexing' ? 'var(--color-warning)' : 'var(--color-success)' }" />
        {{ indexStatusText }}
      </span>
      <span class="status-item" :style="{ color: aiCoreColor }">
        <span class="status-dot" :style="{ background: aiCoreColor }" />
        {{ aiCoreStatusText }}
      </span>
      <span v-if="agentStore.isRunning" class="status-item agent-status">
        <span class="spinner" />
        {{ t('智能体运行中', 'Agent running') }}
      </span>
    </div>
    <div class="statusbar-right">
      <span v-if="defaultProvider" class="status-item provider-info">
        {{ defaultProvider.name }} · {{ defaultProvider.default_model }}
      </span>
      <span v-if="showEditorInfo" class="status-item">
        {{ editorStore.lineCount }} {{ t('行', 'lines') }}
      </span>
      <span v-if="showEditorInfo" class="status-item">
        {{ editorStore.wordCount }} {{ t('字', 'words') }}
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
  padding: 0 var(--space-lg);
  background: var(--color-surface-secondary);
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
  transition: color var(--motion-fast);

  &:hover {
    color: var(--color-text-primary);
  }
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  box-shadow: 0 0 0 2px var(--color-surface-secondary);
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

@media (max-width: 760px) {
  .statusbar-left, .statusbar-right { gap: var(--space-sm); }
  .provider-info { display: none; }
}
</style>
