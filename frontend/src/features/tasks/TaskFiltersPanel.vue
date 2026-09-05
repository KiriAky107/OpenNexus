<script setup lang="ts">
import { useTaskStore } from '@/stores/task'
import { t } from '@/i18n'
const taskStore = useTaskStore()
</script>

<template>
  <div class="sidebar-panel filters">
    <div class="field"><label>{{ t('状态', 'Status') }}</label><select v-model="taskStore.filterStatus" class="select"><option value="all">{{ t('全部', 'All') }}</option><option value="todo">{{ t('待办', 'To do') }}</option><option value="in_progress">{{ t('进行中', 'In progress') }}</option><option value="done">{{ t('已完成', 'Completed') }}</option><option value="cancelled">{{ t('已取消', 'Cancelled') }}</option></select></div>
    <div class="task-counts"><p><span>{{ t('待办', 'To do') }}</span><strong>{{ taskStore.todoTasks.length }}</strong></p><p><span>{{ t('进行中', 'In progress') }}</span><strong>{{ taskStore.inProgressTasks.length }}</strong></p><p><span>{{ t('已完成', 'Completed') }}</span><strong>{{ taskStore.doneTasks.length }}</strong></p></div>
  </div>
</template>

<style scoped>
.filters { display: grid; gap: var(--space-md); }
.task-counts { display: grid; gap: var(--space-xs); padding-top: var(--space-md); border-top: 1px solid var(--color-border-subtle); }
.task-counts p { display: flex; justify-content: space-between; color: var(--color-text-secondary); }
</style>
