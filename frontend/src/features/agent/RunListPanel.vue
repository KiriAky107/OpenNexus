<script setup lang="ts">
import { computed, watch, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAgentStore } from '@/stores/agent'
import { localeTag, t } from '@/i18n'
import { runStatusLabel } from './labels'

const agentStore = useAgentStore()
const router = useRouter()
const error = ref('')
const page = ref(1)
const pages = computed(() => Math.max(1, Math.ceil(agentStore.sortedRuns.length / 50)))
const visibleRuns = computed(() => agentStore.sortedRuns.slice((page.value - 1) * 50, page.value * 50))
watch(pages, count => { page.value = Math.min(page.value, count) })

onMounted(async () => {
  try { await agentStore.loadRuns() } catch (reason) { error.value = reason instanceof Error ? reason.message : t('运行记录加载失败', 'Failed to load runs') }
})

function selectRun(runId: string) { void router.push({ name: 'agent', params: { runId } }) }
</script>

<template>
  <div class="sidebar-panel">
    <button class="button-primary new-button" @click="router.push({ name: 'agent' })">＋ {{ t('新建运行', 'New run') }}</button>
    <p v-if="error" class="subtle error-text">{{ error }}</p>
    <div v-if="pages > 1" class="inline-actions"><button class="button-secondary" :disabled="page === 1" @click="page--">{{ t('上一页', 'Previous') }}</button><span>{{ page }} / {{ pages }}</span><button class="button-secondary" :disabled="page === pages" @click="page++">{{ t('下一页', 'Next') }}</button></div>
    <div class="sidebar-list">
      <button v-for="run in visibleRuns" :key="run.run_id" class="sidebar-list-item run-item"
        :class="{ active: agentStore.activeRunId === run.run_id }" @click="selectRun(run.run_id)">
        <span class="badge" :class="{ success: run.status === 'completed', error: run.status === 'failed', warning: run.status === 'waiting_permission' }">{{ runStatusLabel(run.status) }}</span>
        <strong :title="run.input">{{ run.input || t('智能体任务', 'Agent task') }}</strong><small>{{ run.started_at ? new Date(run.started_at).toLocaleString(localeTag()) : t('等待开始', 'Waiting to start') }}</small>
      </button>
    </div>
  </div>
</template>

<style scoped>
.new-button { width: 100%; margin-bottom: var(--space-md); }
.run-item { display: grid; gap: 3px; width: 100%; text-align: left; }
.run-item .badge { justify-self: start; }
.run-item strong { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.run-item small { color: var(--color-text-tertiary); }
.error-text { margin-bottom: var(--space-sm); color: var(--color-error); }
</style>
