<script setup lang="ts">
import { useTaskStore } from '@/stores/task'
const taskStore = useTaskStore()
</script>

<template>
  <div class="sidebar-panel filters">
    <div class="field"><label>状态</label><select v-model="taskStore.filterStatus" class="select"><option value="all">全部</option><option value="todo">待办</option><option value="in_progress">进行中</option><option value="done">已完成</option><option value="cancelled">已取消</option></select></div>
    <div class="task-counts"><p><span>待办</span><strong>{{ taskStore.todoTasks.length }}</strong></p><p><span>进行中</span><strong>{{ taskStore.inProgressTasks.length }}</strong></p><p><span>已完成</span><strong>{{ taskStore.doneTasks.length }}</strong></p></div>
  </div>
</template>

<style scoped>
.filters { display: grid; gap: var(--space-md); }
.task-counts { display: grid; gap: var(--space-xs); padding-top: var(--space-md); border-top: 1px solid var(--color-border-subtle); }
.task-counts p { display: flex; justify-content: space-between; color: var(--color-text-secondary); }
</style>
