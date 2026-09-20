<script setup lang="ts">
import ActionDialog from "@/components/common/ActionDialog.vue";
import { useActionDialog } from "@/composables/useActionDialog";
const { actionDialog, resolveAction, askConfirm } = useActionDialog();
import AppDialog from "@/components/common/AppDialog.vue";
import MarkdownContent from "@/components/common/MarkdownContent.vue";
import { computed, watch, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import type { TaskAgentScheduleInput, TaskItem, TaskStatus } from "@/contracts";
import { useTaskStore } from "@/stores/task";
import { useProviderStore } from "@/stores/provider";
import { useSkillStore } from "@/stores/skill";
import { localeTag, t } from "@/i18n";

const taskStore = useTaskStore();
const providerStore = useProviderStore();
const skillStore = useSkillStore();
const router = useRouter();
const page = ref(1);
const pageCount = computed(() =>
  Math.max(1, Math.ceil(taskStore.filteredTasks.length / 100)),
);
const visibleTasks = computed(() =>
  taskStore.filteredTasks.slice((page.value - 1) * 100, page.value * 100),
);
watch(
  () => [
    taskStore.filterStatus,
    taskStore.filterPriority,
    taskStore.filterSource,
  ],
  () => {
    page.value = 1;
  },
);
watch(pageCount, (count) => {
  page.value = Math.min(page.value, count);
});
const showForm = ref(false);
const editingId = ref<string | null>(null);
const actionError = ref("");
const form = reactive({
  title: "",
  description: "",
  due_date: "",
  note_id: "",
  schedule_enabled: false,
  schedule_type: "once" as "once" | "cron",
  run_at: "",
  cron: "0 9 * * *",
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  provider_id: "",
  model: "",
  skill_id: "",
  max_steps: 10,
  allow_network: false,
});
const models = computed(
  () => providerStore.modelsByProvider[form.provider_id] ?? [],
);

onMounted(async () => {
  await Promise.all([
    taskStore.loadTasks(),
    providerStore.loadProviders(),
    skillStore.loadSkills(),
  ]);
  if (!form.provider_id) form.provider_id = providerStore.defaultProviderId;
});

watch(
  () => form.provider_id,
  async (providerId) => {
    if (!providerId) return;
    const provider = providerStore.providers.find(
      (item) => item.provider_id === providerId,
    );
    if (!form.model) form.model = provider?.default_model ?? "";
    try {
      await providerStore.loadModels(providerId);
    } catch {
      /* 仍允许手动输入模型 ID */
    }
  },
);

function resetForm() {
  editingId.value = null;
  Object.assign(form, {
    title: "",
    description: "",
    due_date: "",
    note_id: "",
    schedule_enabled: false,
    schedule_type: "once",
    run_at: "",
    cron: "0 9 * * *",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    provider_id: providerStore.defaultProviderId,
    model: "",
    skill_id: "",
    max_steps: 10,
    allow_network: false,
  });
}
function editTask(task: TaskItem) {
  const schedule = task.agent_schedule;
  editingId.value = task.task_id;
  Object.assign(form, {
    title: task.title,
    description: task.description ?? "",
    due_date: task.due_date?.slice(0, 16) ?? "",
    note_id: task.note_id ?? "",
    schedule_enabled: Boolean(
      schedule?.enabled && schedule.status === "pending",
    ),
    schedule_type: schedule?.schedule_type ?? "once",
    run_at: schedule?.run_at
      ? new Date(schedule.run_at).toISOString().slice(0, 16)
      : "",
    cron: schedule?.cron ?? "0 9 * * *",
    timezone:
      schedule?.timezone ??
      Intl.DateTimeFormat().resolvedOptions().timeZone ??
      "UTC",
    provider_id: schedule?.provider_id ?? providerStore.defaultProviderId,
    model: schedule?.model ?? "",
    skill_id: schedule?.skill_id ?? "",
    max_steps: schedule?.max_steps ?? 10,
    allow_network: schedule?.allow_network ?? false,
  });
  showForm.value = true;
}

function scheduleInput(): TaskAgentScheduleInput | null {
  if (!form.schedule_enabled) return null;
  if (!form.provider_id || !form.model.trim())
    throw new Error(
      t(
        "定时 Agent 需要模型提供商和模型。",
        "Scheduled Agent requires a provider and model.",
      ),
    );
  if (form.schedule_type === "once" && !form.run_at)
    throw new Error(
      t("请设置一次性任务的执行时间。", "Set the one-time execution time."),
    );
  if (form.schedule_type === "cron" && !form.cron.trim())
    throw new Error(t("请填写 Cron 表达式。", "Enter a Cron expression."));
  return {
    schedule_type: form.schedule_type,
    run_at:
      form.schedule_type === "once"
        ? new Date(form.run_at).toISOString()
        : null,
    cron: form.schedule_type === "cron" ? form.cron.trim() : null,
    timezone: form.timezone,
    enabled: true,
    provider_id: form.provider_id,
    model: form.model.trim(),
    skill_id: form.skill_id || null,
    max_steps: form.max_steps,
    allow_network: form.allow_network,
  };
}

async function saveTask() {
  actionError.value = "";
  try {
    const agent_schedule = scheduleInput();
    const values = {
      title: form.title,
      description: form.description,
      due_date: form.due_date || undefined,
      note_id: form.note_id || null,
      agent_schedule,
    };
    if (editingId.value) await taskStore.updateTask(editingId.value, values);
    else
      await taskStore.createTask({
        ...values,
        note_id: form.note_id || undefined,
        agent_schedule: agent_schedule || undefined,
      });
    showForm.value = false;
    resetForm();
  } catch (error) {
    actionError.value =
      error instanceof Error
        ? error.message
        : t("任务保存失败", "Failed to save task");
  }
}

async function setStatus(task: TaskItem, status: TaskStatus) {
  try {
    await taskStore.updateTask(task.task_id, { status });
  } catch (error) {
    actionError.value =
      error instanceof Error
        ? error.message
        : t("状态更新失败", "Failed to update status");
  }
}

async function remove(task: TaskItem) {
  if (
    !(await askConfirm(`${t("确定删除任务", "Delete task")} “${task.title}”?`))
  )
    return;
  try {
    await taskStore.deleteTask(task.task_id);
  } catch (error) {
    actionError.value =
      error instanceof Error
        ? error.message
        : t("任务删除失败", "Failed to delete task");
  }
}
</script>

<template>
  <section class="feature-page tasks-page">
    <ActionDialog
      v-if="actionDialog"
      v-bind="actionDialog"
      @resolve="resolveAction"
    />
    <header class="feature-header">
      <div>
        <h1>{{ t("任务", "Tasks") }}</h1>
        <p>
          {{
            t(
              "管理用户、笔记和 Agent 产生的行动项。",
              "Manage action items created by users, notes, and agents.",
            )
          }}
        </p>
      </div>
      <button
        class="button-primary"
        @click="
          resetForm();
          showForm = true;
        "
      >
        ＋ {{ t("新建任务", "New task") }}
      </button>
    </header>
    <div v-if="taskStore.error || actionError" class="error-banner">
      {{ taskStore.error || actionError }}
    </div>
    <div v-if="taskStore.filteredTasks.length" class="task-list">
      <article
        v-for="task in visibleTasks"
        :key="task.task_id"
        class="item-card task-card"
      >
        <button
          class="status-check"
          :class="{ done: task.status === 'done' }"
          :title="t('切换完成状态', 'Toggle completion')"
          @click="setStatus(task, task.status === 'done' ? 'todo' : 'done')"
        >
          {{ task.status === "done" ? "✓" : "" }}
        </button>
        <div class="task-content">
          <div class="task-title">
            <strong :class="{ completed: task.status === 'done' }">{{
              task.title
            }}</strong>
          </div>
          <MarkdownContent
            v-if="task.description"
            class="task-markdown"
            :source="task.description"
          />
          <div class="subtle">
            <span>{{ task.status }}</span
            ><span v-if="task.due_date"
              >{{ t("截止", "Due") }}
              {{ new Date(task.due_date).toLocaleString(localeTag()) }}</span
            ><span v-if="task.note_id"
              >{{ t("关联 Note", "Linked Note") }}: {{ task.note_id }}</span
            ><span v-if="task.agent_schedule"
              >{{
                task.agent_schedule.schedule_type === "cron"
                  ? `Cron ${task.agent_schedule.cron}`
                  : t("一次性 Agent", "One-time Agent")
              }}
              · {{ task.agent_schedule.status }}</span
            ><span v-if="task.agent_schedule?.next_run_at"
              >{{ t("下次执行", "Next run") }}
              {{
                new Date(task.agent_schedule.next_run_at).toLocaleString(
                  localeTag(),
                )
              }}</span
            ><button
              v-if="task.agent_schedule?.last_run_id"
              class="link-button"
              @click="
                router.push({
                  name: 'agent',
                  params: { runId: task.agent_schedule!.last_run_id },
                })
              "
            >
              {{ t("查看 Agent", "View Agent") }}
            </button>
          </div>
          <p v-if="task.agent_schedule?.error" class="error-text">
            {{ task.agent_schedule.error }}
          </p>
        </div>
        <div class="inline-actions">
          <button class="icon-button" @click="editTask(task)">
            {{ t("编辑", "Edit") }}</button
          ><button class="button-danger" @click="remove(task)">
            {{ t("删除", "Delete") }}
          </button>
        </div>
      </article>
    </div>
    <div v-else class="empty-state">
      <div>
        <strong>{{
          taskStore.isLoading
            ? t("正在加载任务…", "Loading tasks…")
            : t("没有符合条件的任务", "No matching tasks")
        }}</strong>
        <p>
          {{
            t(
              "创建一项任务，或调整左侧筛选条件。",
              "Create a task or adjust the filters.",
            )
          }}
        </p>
      </div>
    </div>
    <nav v-if="pageCount > 1" class="inline-actions">
      <button class="button-secondary" :disabled="page === 1" @click="page--">
        {{ t("上一页", "Previous") }}</button
      ><span
        >{{ page }} / {{ pageCount }} ·
        {{ taskStore.filteredTasks.length }}</span
      ><button
        class="button-secondary"
        :disabled="page === pageCount"
        @click="page++"
      >
        {{ t("下一页", "Next") }}
      </button>
    </nav>
    <AppDialog
      v-if="showForm"
      :label="t('任务表单', 'Task form')"
      @close="showForm = false"
      ><div class="modal task-modal">
        <h2>
          {{
            editingId ? t("编辑任务", "Edit task") : t("新建任务", "New task")
          }}
        </h2>
        <form @submit.prevent="saveTask">
          <div class="field">
            <label>{{ t("标题", "Title") }}</label
            ><input v-model="form.title" class="input" required />
          </div>
          <div class="field">
            <label>{{ t("内容（Markdown）", "Content (Markdown)") }}</label
            ><textarea
              v-model="form.description"
              class="textarea task-source"
              :placeholder="
                t(
                  '支持标题、列表、代码块、Mermaid 和公式',
                  'Supports headings, lists, code blocks, Mermaid and math',
                )
              "
            />
          </div>
          <div class="field">
            <label>{{ t("截止时间", "Due date") }}</label
            ><input
              v-model="form.due_date"
              class="input"
              type="datetime-local"
            />
          </div>
          <div class="field">
            <label>{{ t("关联 Note ID", "Linked Note ID") }}</label
            ><input v-model="form.note_id" class="input" />
          </div>
          <label class="schedule-toggle"
            ><input v-model="form.schedule_enabled" type="checkbox" />
            {{ t("定时调用 Agent", "Schedule Agent") }}</label
          >
          <fieldset v-if="form.schedule_enabled" class="schedule-fields">
            <legend>{{ t("Agent 调度", "Agent schedule") }}</legend>
            <div class="form-grid">
              <div class="field">
                <label>{{ t("调度类型", "Schedule type") }}</label
                ><select v-model="form.schedule_type" class="select">
                  <option value="once">{{ t("一次性", "One time") }}</option>
                  <option value="cron">Cron</option>
                </select>
              </div>
              <div v-if="form.schedule_type === 'once'" class="field">
                <label>{{ t("执行时间", "Run at") }}</label
                ><input
                  v-model="form.run_at"
                  class="input"
                  type="datetime-local"
                />
              </div>
              <div v-else class="field">
                <label>Cron</label
                ><input
                  v-model="form.cron"
                  class="input"
                  placeholder="0 9 * * *"
                /><small class="subtle">{{
                  t(
                    "5 段 Cron：分 时 日 月 周",
                    "5-field Cron: minute hour day month weekday",
                  )
                }}</small>
              </div>
              <div class="field">
                <label>{{ t("时区", "Time zone") }}</label
                ><input v-model="form.timezone" class="input" />
              </div>
              <div class="field">
                <label>{{ t("模型提供商", "Model provider") }}</label
                ><select v-model="form.provider_id" class="select">
                  <option
                    v-for="provider in providerStore.enabledProviders"
                    :key="provider.provider_id"
                    :value="provider.provider_id"
                  >
                    {{ provider.name }}
                  </option>
                </select>
              </div>
              <div class="field">
                <label>{{ t("模型", "Model") }}</label
                ><input
                  v-model="form.model"
                  class="input"
                  list="task-agent-models"
                /><datalist id="task-agent-models">
                  <option
                    v-for="model in models"
                    :key="model.model_id"
                    :value="model.model_id"
                  >
                    {{ model.name }}
                  </option>
                </datalist>
              </div>
              <div class="field">
                <label>Skill</label
                ><select v-model="form.skill_id" class="select">
                  <option value="">{{ t("不使用 Skill", "No Skill") }}</option>
                  <option
                    v-for="skill in skillStore.readySkills"
                    :key="skill.skill_id"
                    :value="skill.skill_id"
                  >
                    {{ skill.name }}
                  </option>
            <option
              v-for="skill in skillStore.readyUserSkills"
              :key="skill.skill_id"
              :value="skill.skill_id"
            >
                    {{ skill.data.name }}
                  </option>
                </select>
              </div>
              <div class="field">
                <label>{{ t("最大步骤", "Maximum steps") }}</label
                ><input
                  v-model.number="form.max_steps"
                  class="input"
                  type="number"
                  min="1"
                  max="100"
                />
              </div>
            </div>
            <label
              ><input v-model="form.allow_network" type="checkbox" />
              {{ t("允许网络工具", "Allow network tools") }}</label
            >
            <p class="subtle">
              {{
                t(
                  "调度在应用打开时执行；关闭期间错过的任务会在下次打开知识库后补执行。",
                  "Schedules run while the app is open; overdue runs are resumed when the vault is opened again.",
                )
              }}
            </p>
          </fieldset>
          <div class="inline-actions">
            <button class="button-primary">{{ t("保存", "Save") }}</button
            ><button
              type="button"
              class="button-secondary"
              @click="showForm = false"
            >
              {{ t("取消", "Cancel") }}
            </button>
          </div>
        </form>
      </div></AppDialog
    >
  </section>
</template>

<style scoped>
.tasks-page > :is(.feature-header, .task-list, .empty-state, .error-banner) {
  width: 100%;
  max-width: 1180px;
  margin-inline: auto;
  box-sizing: border-box;
}
.task-content {
  min-width: 0;
  overflow-wrap: anywhere;
}
.task-list {
  display: grid;
  gap: var(--space-md);
  width: min(100%, 1180px);
  margin-inline: auto;
}
.task-card {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: var(--space-md);
}
.status-check {
  width: 28px;
  height: 28px;
  border: 2px solid var(--color-border-default);
  border-radius: var(--radius-full);
  transition:
    border-color var(--motion-fast),
    background-color var(--motion-fast),
    color var(--motion-fast),
    transform var(--motion-fast);
}
.status-check:hover {
  border-color: var(--color-success);
  transform: scale(1.06);
}
.status-check.done {
  border-color: var(--color-success);
  background: var(--color-success);
  color: var(--color-on-success);
  box-shadow: 0 3px 10px
    color-mix(in srgb, var(--color-success) 24%, transparent);
}
.task-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-sm);
}
.task-content p {
  margin: var(--space-xs) 0;
}
.task-markdown {
  margin-block: var(--space-xs);
}
.task-modal {
  width: min(820px, calc(100vw - 32px));
  max-height: calc(100vh - 48px);
  overflow: auto;
}
.task-source {
  min-height: 150px;
  font-family: var(--font-ui-mono);
}
.schedule-toggle {
  display: flex;
  gap: var(--space-sm);
  margin-block: var(--space-md);
}
.schedule-fields {
  padding: var(--space-md);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
}
.schedule-fields .form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-md);
}
.link-button {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-text-link);
  cursor: pointer;
}
.error-text {
  color: var(--color-error);
}
.task-content .subtle {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-md);
}
.completed {
  text-decoration: line-through;
  color: var(--color-text-tertiary);
}
@media (max-width: 700px) {
  .task-card {
    grid-template-columns: auto 1fr;
  }
  .task-card > .inline-actions {
    grid-column: 2;
  }
  .schedule-fields .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
