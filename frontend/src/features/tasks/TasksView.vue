<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import type { TaskItem, TaskStatus } from '@/contracts'
import { useTaskStore } from '@/stores/task'
import { localeTag, t } from '@/i18n'

const taskStore = useTaskStore()
const showForm = ref(false)
const editingId = ref<string | null>(null)
const actionError = ref('')
const form = reactive({ title: '', description: '', due_date: '', note_id: '' })

onMounted(() => { void taskStore.loadTasks() })

function resetForm() { editingId.value = null; form.title = ''; form.description = ''; form.due_date = ''; form.note_id = '' }
function editTask(task: TaskItem) { editingId.value = task.task_id; Object.assign(form, { title: task.title, description: task.description ?? '', due_date: task.due_date?.slice(0, 16) ?? '', note_id: task.note_id ?? '' }); showForm.value = true }

async function saveTask() {
  actionError.value = ''
  try {
    if (editingId.value) await taskStore.updateTask(editingId.value, { ...form, due_date: form.due_date || undefined, note_id: form.note_id || null })
    else await taskStore.createTask({ ...form, due_date: form.due_date || undefined, note_id: form.note_id || undefined })
    showForm.value = false; resetForm()
  } catch (error) { actionError.value = error instanceof Error ? error.message : t('任务保存失败', 'Failed to save task') }
}

async function setStatus(task: TaskItem, status: TaskStatus) {
  try { await taskStore.updateTask(task.task_id, { status }) } catch (error) { actionError.value = error instanceof Error ? error.message : t('状态更新失败', 'Failed to update status') }
}

async function remove(task: TaskItem) {
  if (!confirm(`${t('确定删除任务', 'Delete task')} “${task.title}”?`)) return
  try { await taskStore.deleteTask(task.task_id) } catch (error) { actionError.value = error instanceof Error ? error.message : t('任务删除失败', 'Failed to delete task') }
}
</script>

<template>
  <section class="feature-page tasks-page">
    <header class="feature-header"><div><h1>{{ t('任务', 'Tasks') }}</h1><p>{{ t('管理用户、笔记和 Agent 产生的行动项。', 'Manage action items created by users, notes, and agents.') }}</p></div><button class="button-primary" @click="resetForm(); showForm = true">＋ {{ t('新建任务', 'New task') }}</button></header>
    <div v-if="taskStore.error || actionError" class="error-banner">{{ taskStore.error || actionError }}</div>
    <div v-if="taskStore.filteredTasks.length" class="task-list">
      <article v-for="task in taskStore.filteredTasks" :key="task.task_id" class="item-card task-card">
        <button class="status-check" :class="{ done: task.status === 'done' }" :title="t('切换完成状态', 'Toggle completion')" @click="setStatus(task, task.status === 'done' ? 'todo' : 'done')">{{ task.status === 'done' ? '✓' : '' }}</button>
        <div class="task-content"><div class="task-title"><strong :class="{ completed: task.status === 'done' }">{{ task.title }}</strong></div><p v-if="task.description" class="muted">{{ task.description }}</p><div class="subtle"><span>{{ task.status }}</span><span v-if="task.due_date">{{ t('截止', 'Due') }} {{ new Date(task.due_date).toLocaleString(localeTag()) }}</span><span v-if="task.note_id">{{ t('关联 Note', 'Linked Note') }}: {{ task.note_id }}</span></div></div>
        <div class="inline-actions"><button class="icon-button" @click="editTask(task)">{{ t('编辑', 'Edit') }}</button><button class="button-danger" @click="remove(task)">{{ t('删除', 'Delete') }}</button></div>
      </article>
    </div>
    <div v-else class="empty-state"><div><strong>{{ taskStore.isLoading ? t('正在加载任务…', 'Loading tasks…') : t('没有符合条件的任务', 'No matching tasks') }}</strong><p>{{ t('创建一项任务，或调整左侧筛选条件。', 'Create a task or adjust the filters.') }}</p></div></div>
    <div v-if="showForm" class="modal-backdrop" @click.self="showForm = false"><div class="modal"><h2>{{ editingId ? t('编辑任务', 'Edit task') : t('新建任务', 'New task') }}</h2><form @submit.prevent="saveTask"><div class="field"><label>{{ t('标题', 'Title') }}</label><input v-model="form.title" class="input" required /></div><div class="field"><label>{{ t('描述', 'Description') }}</label><textarea v-model="form.description" class="textarea" /></div><div class="field"><label>{{ t('截止时间', 'Due date') }}</label><input v-model="form.due_date" class="input" type="datetime-local" /></div><div class="field"><label>{{ t('关联 Note ID', 'Linked Note ID') }}</label><input v-model="form.note_id" class="input" /></div><div class="inline-actions"><button class="button-primary">{{ t('保存', 'Save') }}</button><button type="button" class="button-secondary" @click="showForm = false">{{ t('取消', 'Cancel') }}</button></div></form></div></div>
  </section>
</template>

<style scoped>
.tasks-page > :is(.feature-header, .task-list, .empty-state, .error-banner) { width: 100%; max-width: 1180px; margin-inline: auto; box-sizing: border-box; }
.task-content { min-width: 0; overflow-wrap: anywhere; }
.task-list { display: grid; gap: var(--space-md); width: min(100%, 1180px); margin-inline: auto; }
.task-card { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: var(--space-md); }
.status-check { width: 28px; height: 28px; border: 2px solid var(--color-border-default); border-radius: var(--radius-full); transition: border-color var(--motion-fast), background-color var(--motion-fast), color var(--motion-fast), transform var(--motion-fast); }
.status-check:hover { border-color: var(--color-success); transform: scale(1.06); }
.status-check.done { border-color: var(--color-success); background: var(--color-success); color: white; box-shadow: 0 3px 10px color-mix(in srgb, var(--color-success) 24%, transparent); }
.task-title { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-sm); }
.task-content p { margin: var(--space-xs) 0; }
.task-content .subtle { display: flex; flex-wrap: wrap; gap: var(--space-md); }
.completed { text-decoration: line-through; color: var(--color-text-tertiary); }
@media (max-width: 700px) { .task-card { grid-template-columns: auto 1fr; } .task-card > .inline-actions { grid-column: 2; } }
</style>
