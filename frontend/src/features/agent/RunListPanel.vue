<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAgentStore } from '@/stores/agent'
import { runStatusLabel } from './labels'

const agentStore = useAgentStore()
const router = useRouter()
const error = ref('')

onMounted(async () => {
  try { await agentStore.loadRuns() } catch (reason) { error.value = reason instanceof Error ? reason.message : '运行记录加载失败' }
})

function selectRun(runId: string) { void router.push({ name: 'agent', params: { runId } }) }
</script>

<template>
  <div class="sidebar-panel">
    <button class="button-primary new-button" @click="router.push({ name: 'agent' })">＋ 新建运行</button>
    <p v-if="error" class="subtle error-text">{{ error }}</p>
    <div class="sidebar-list">
      <button v-for="run in agentStore.sortedRuns" :key="run.run_id" class="sidebar-list-item run-item"
        :class="{ active: agentStore.activeRunId === run.run_id }" @click="selectRun(run.run_id)">
        <span class="badge" :class="{ success: run.status === 'completed', error: run.status === 'failed', warning: run.status === 'waiting_permission' }">{{ runStatusLabel(run.status) }}</span>
        <strong>{{ run.run_id.slice(0, 12) }}</strong><small>{{ run.started_at ? new Date(run.started_at).toLocaleString() : '等待开始' }}</small>
      </button>
    </div>
  </div>
</template>

<style scoped>
.new-button { width: 100%; margin-bottom: var(--space-md); }
.run-item { display: grid; gap: 3px; width: 100%; text-align: left; }
.run-item .badge { justify-self: start; }
.run-item small { color: var(--color-text-tertiary); }
.error-text { margin-bottom: var(--space-sm); color: var(--color-error); }
</style>
