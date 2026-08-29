<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import type { TaskItem, TaskStatus } from '@/contracts'
import { useTaskStore } from '@/stores/task'

const taskStore = useTaskStore()
const showForm = ref(false)
const editingId = ref<string | null>(null)
const actionError = ref('')
const form = reactive({ title: '', description: '', priority: 'medium' as TaskItem['priority'], due_date: '', note_id: '' })

onMounted(() => { void taskStore.loadTasks() })

function resetForm() { editingId.value = null; form.title = ''; form.description = ''; form.priority = 'medium'; form.due_date = ''; form.note_id = '' }
function editTask(task: TaskItem) { editingId.value = task.task_id; Object.assign(form, { title: task.title, description: task.description ?? '', priority: task.priority, due_date: task.due_date?.slice(0, 16) ?? '', note_id: task.note_id ?? '' }); showForm.value = true }

async function saveTask() {
  actionError.value = ''
  try {
    if (editingId.value) await taskStore.updateTask(editingId.value, { ...form, due_date: form.due_date || undefined })
    else await taskStore.createTask({ ...form, due_date: form.due_date || undefined, note_id: form.note_id || undefined })
    showForm.value = false; resetForm()
  } catch (error) { actionError.value = error instanceof Error ? error.message : '任务保存失败' }
}

async function setStatus(task: TaskItem, status: TaskStatus) {
  try { await taskStore.updateTask(task.task_id, { status }) } catch (error) { actionError.value = error instanceof Error ? error.message : '状态更新失败' }
}

async function remove(task: TaskItem) {
  if (!confirm(`确定删除任务“${task.title}”吗？`)) return
  try { await taskStore.deleteTask(task.task_id) } catch (error) { actionError.value = error instanceof Error ? error.message : '任务删除失败' }
}
</script>

<template>
  <section class="feature-page">
    <header class="feature-header"><div><h1>任务</h1><p>管理用户、笔记和 Agent 产生的行动项。</p></div><button class="button-primary" @click="resetForm(); showForm = true">＋ 新建任务</button></header>
    <div v-if="taskStore.error || actionError" class="error-banner">{{ taskStore.error || actionError }}</div>
    <div v-if="taskStore.filteredTasks.length" class="task-list">
      <article v-for="task in taskStore.filteredTasks" :key="task.task_id" class="item-card task-card">
        <button class="status-check" :class="{ done: task.status === 'done' }" title="切换完成状态" @click="setStatus(task, task.status === 'done' ? 'todo' : 'done')">{{ task.status === 'done' ? '✓' : '' }}</button>
        <div class="task-content"><div class="task-title"><strong :class="{ completed: task.status === 'done' }">{{ task.title }}</strong><span class="badge" :class="{ error: task.priority === 'high', warning: task.priority === 'medium' }">{{ task.priority }}</span><span class="badge info">{{ task.source }}</span></div><p v-if="task.description" class="muted">{{ task.description }}</p><div class="subtle"><span>{{ task.status }}</span><span v-if="task.due_date">截止 {{ new Date(task.due_date).toLocaleString() }}</span><span v-if="task.note_title">关联：{{ task.note_title }}</span></div></div>
        <div class="inline-actions"><button class="icon-button" @click="editTask(task)">编辑</button><button class="button-danger" @click="remove(task)">删除</button></div>
      </article>
    </div>
    <div v-else class="empty-state"><div><strong>{{ taskStore.isLoading ? '正在加载任务…' : '没有符合条件的任务' }}</strong><p>创建一项任务，或调整左侧筛选条件。</p></div></div>
    <div v-if="showForm" class="modal-backdrop" @click.self="showForm = false"><div class="modal"><h2>{{ editingId ? '编辑任务' : '新建任务' }}</h2><form @submit.prevent="saveTask"><div class="field"><label>标题</label><input v-model="form.title" class="input" required /></div><div class="field"><label>描述</label><textarea v-model="form.description" class="textarea" /></div><div class="form-grid"><div class="field"><label>优先级</label><select v-model="form.priority" class="select"><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></div><div class="field"><label>截止时间</label><input v-model="form.due_date" class="input" type="datetime-local" /></div></div><div class="field"><label>关联 Note ID</label><input v-model="form.note_id" class="input" /></div><div class="inline-actions"><button class="button-primary">保存</button><button type="button" class="button-secondary" @click="showForm = false">取消</button></div></form></div></div>
  </section>
</template>

<style scoped>
.task-list { display: grid; gap: var(--space-md); }
.task-card { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: var(--space-md); }
.status-check { width: 26px; height: 26px; border: 2px solid var(--color-border-default); border-radius: var(--radius-full); }
.status-check.done { border-color: var(--color-success); background: var(--color-success); color: white; }
.task-title { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-sm); }
.task-content p { margin: var(--space-xs) 0; }
.task-content .subtle { display: flex; flex-wrap: wrap; gap: var(--space-md); }
.completed { text-decoration: line-through; color: var(--color-text-tertiary); }
@media (max-width: 700px) { .task-card { grid-template-columns: auto 1fr; } .task-card > .inline-actions { grid-column: 2; } }
</style>
