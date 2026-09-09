<script setup lang="ts">
import ActionDialog from '@/components/common/ActionDialog.vue'
import { useActionDialog } from '@/composables/useActionDialog'
import { t } from '@/i18n'
import { useSkillStore } from '@/stores/skill'
import { useWorkspaceStore } from '@/stores/workspace'
import type { UserSkill, UserSkillWriteRequest } from '@/contracts'
import { computed, reactive, ref, watch } from 'vue'

const permissions = [
  'notes.read', 'notes.search', 'notes.write', 'notes.delete', 'tasks.read', 'tasks.write',
  'attachments.read', 'network.request', 'secrets.use', 'ui.command', 'ui.settings', 'ui.sidebar',
]
const capabilities = [
  'chat', 'vision', 'tool_calling', 'reasoning', 'streaming', 'structured_output',
  'embedding', 'transcription', 'speaker_matching',
]
const skillStore = useSkillStore()
const workspace = useWorkspaceStore()
const { actionDialog, resolveAction, askConfirm } = useActionDialog()
const editingId = ref<string | null>(null)
const loadedVault = ref(workspace.vaultId)
const busy = ref(false)
const error = ref('')
let pendingSave: { fingerprint: string; operationId: string } | null = null
const pendingDeletes = new Map<string, { revision: string; operationId: string }>()
const editingSkill = computed(() => skillStore.userSkills.find(skill => skill.skill_id === editingId.value) ?? null)
const form = reactive({
  revision: '', name: '', description: '', prompt: '', tools: '', permissions: [] as string[],
  capabilities: [] as string[], topK: 10, rerank: true, citation: true,
})

function reset() {
  editingId.value = null
  Object.assign(form, { revision: '', name: '', description: '', prompt: '', tools: '', permissions: [], capabilities: [], topK: 10, rerank: true, citation: true })
  error.value = ''
  pendingSave = null
}

function edit(skill: UserSkill) {
  editingId.value = skill.skill_id
  Object.assign(form, {
    revision: skill.revision,
    name: skill.data.name,
    description: skill.data.description,
    prompt: skill.data.prompt,
    tools: skill.data.tools.join(', '),
    permissions: [...skill.data.permissions],
    capabilities: [...skill.data.required_capabilities],
    topK: skill.data.retrieval.top_k,
    rerank: skill.data.retrieval.rerank,
    citation: skill.data.retrieval.citation,
  })
  error.value = ''
  pendingSave = null
}

function payload(): UserSkillWriteRequest {
  return {
    revision: form.revision,
    name: form.name,
    description: form.description,
    prompt: form.prompt,
    tools: [...new Set(form.tools.split(',').map(value => value.trim()).filter(Boolean))],
    permissions: [...form.permissions],
    retrieval: { top_k: form.topK, rerank: form.rerank, citation: form.citation },
    required_capabilities: [...form.capabilities],
  }
}

async function save() {
  const vault = loadedVault.value
  if (!vault || workspace.vaultId !== vault) { error.value = t('工作区已切换，请重新加载。', 'The workspace changed; reload the form.'); return }
  busy.value = true; error.value = ''
  try {
    const request = payload()
    const fingerprint = JSON.stringify({ vault, skillId: editingId.value, request })
    if (pendingSave?.fingerprint !== fingerprint) pendingSave = { fingerprint, operationId: crypto.randomUUID() }
    const saved = editingId.value
      ? await skillStore.updateUserSkill(editingId.value, request, vault, pendingSave.operationId)
      : await skillStore.createUserSkill(request, vault, pendingSave.operationId)
    if (workspace.vaultId === vault) { pendingSave = null; edit(saved) }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('用户 Skill 保存失败', 'Failed to save user Skill')
  } finally { busy.value = false }
}

async function remove(skill: UserSkill) {
  if (!(await askConfirm(`${t('确定删除用户 Skill', 'Delete user Skill')} “${skill.data.name}”?`))) return
  const vault = loadedVault.value
  if (!vault || workspace.vaultId !== vault) return
  busy.value = true; error.value = ''
  try {
    let pending = pendingDeletes.get(skill.skill_id)
    if (!pending || pending.revision !== skill.revision) {
      pending = { revision: skill.revision, operationId: crypto.randomUUID() }
      pendingDeletes.set(skill.skill_id, pending)
    }
    await skillStore.deleteUserSkill(skill.skill_id, skill.revision, vault, pending.operationId)
    pendingDeletes.delete(skill.skill_id)
    if (editingId.value === skill.skill_id) reset()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('用户 Skill 删除失败', 'Failed to delete user Skill')
  } finally { busy.value = false }
}

watch(() => workspace.vaultId, async vault => {
  loadedVault.value = vault; pendingDeletes.clear(); reset()
  if (vault) await skillStore.loadSkills()
}, { flush: 'sync' })
</script>

<template>
  <section class="panel user-skills">
    <ActionDialog v-if="actionDialog" v-bind="actionDialog" @resolve="resolveAction" />
    <header class="section-head">
      <div><h2>{{ t('当前库的用户 Skill', 'User Skills in this Vault') }}</h2><p class="muted">{{ t('提示词和声明式配置会随当前库同步。设备授权、密钥、安装目录和运行状态不会同步；同步到新设备后仍按该设备的权限策略确认。', 'Prompts and declarative settings sync with this Vault. Device grants, secrets, package paths, and runtime state stay local; the destination device still applies its own permission policy.') }}</p></div>
      <button class="button-secondary" :disabled="!workspace.vaultId || busy" @click="reset">{{ t('新建用户 Skill', 'New user Skill') }}</button>
    </header>
    <div v-if="skillStore.userSkillError || error" class="error-banner">{{ error || skillStore.userSkillError }}</div>
    <div v-if="!workspace.vaultId" class="empty-state"><strong>{{ t('请先打开工作区', 'Open a workspace first') }}</strong></div>
    <template v-else>
      <div class="user-skill-grid">
        <button v-for="skill in skillStore.userSkills" :key="skill.skill_id" class="user-skill-card" :class="{ active: editingId === skill.skill_id }" @click="edit(skill)">
          <span><strong>{{ skill.data.name }}</strong><small>v{{ skill.data.version }} · {{ skill.status }}</small></span>
          <span v-if="skill.missing_dependencies.length" class="badge warning">{{ t('缺少工具', 'Missing tools') }}</span>
          <span v-else-if="skill.undeclared_permissions.length" class="badge warning">{{ t('权限声明不足', 'Permission declaration required') }}</span>
          <span v-else class="badge success">{{ t('可选择运行', 'Ready to select') }}</span>
        </button>
      </div>
      <form class="user-skill-form" @submit.prevent="save">
        <div class="form-grid">
          <label class="field"><span>{{ t('名称', 'Name') }}</span><input v-model="form.name" class="input" maxlength="128" required /></label>
          <label class="field"><span>{{ t('工具 ID（逗号分隔）', 'Tool IDs (comma-separated)') }}</span><input v-model="form.tools" class="input" maxlength="8256" placeholder="notes.read, notes.search" /></label>
          <label class="field wide"><span>{{ t('说明', 'Description') }}</span><input v-model="form.description" class="input" maxlength="2000" /></label>
          <label class="field wide"><span>{{ t('系统提示词', 'System prompt') }}</span><textarea v-model="form.prompt" class="textarea prompt" maxlength="64000" rows="8" /></label>
          <label class="field"><span>Top K</span><input v-model.number="form.topK" class="input" type="number" min="1" max="100" required /></label>
          <div class="field checks"><span>{{ t('检索行为', 'Retrieval behavior') }}</span><label><input v-model="form.rerank" type="checkbox" />{{ t('重排', 'Rerank') }}</label><label><input v-model="form.citation" type="checkbox" />{{ t('引用', 'Citations') }}</label></div>
        </div>
        <fieldset><legend>{{ t('权限声明（不是设备授权）', 'Permission declarations (not device grants)') }}</legend><label v-for="permission in permissions" :key="permission" class="check"><input v-model="form.permissions" type="checkbox" :value="permission" />{{ permission }}</label></fieldset>
        <fieldset><legend>{{ t('模型能力要求', 'Required model capabilities') }}</legend><label v-for="capability in capabilities" :key="capability" class="check"><input v-model="form.capabilities" type="checkbox" :value="capability" />{{ capability }}</label></fieldset>
        <div class="inline-actions"><button class="button-primary" :disabled="busy">{{ busy ? t('保存中…', 'Saving…') : t('保存', 'Save') }}</button><button v-if="editingSkill" type="button" class="button-danger" :disabled="busy" @click="remove(editingSkill)">{{ t('删除', 'Delete') }}</button><button type="button" class="button-secondary" :disabled="busy" @click="reset">{{ t('清空表单', 'Clear form') }}</button></div>
      </form>
    </template>
  </section>
</template>

<style scoped>
.user-skills { margin-bottom: var(--space-xl); }
.section-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-lg); margin-bottom: var(--space-lg); }
.section-head p { max-width: 820px; margin-top: var(--space-xs); line-height: 1.5; }
.user-skill-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: var(--space-sm); margin-bottom: var(--space-lg); }
.user-skill-card { display: flex; justify-content: space-between; gap: var(--space-sm); align-items: center; padding: var(--space-md); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-background-secondary); color: inherit; text-align: left; cursor: pointer; }
.user-skill-card.active { border-color: var(--color-accent-primary); box-shadow: 0 0 0 2px var(--color-accent-soft); }
.user-skill-card span:first-child { display: grid; gap: 4px; }
.user-skill-card small { color: var(--color-text-tertiary); }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-md); }
.wide { grid-column: 1 / -1; }
.prompt { min-height: 180px; }
.checks { display: flex; flex-wrap: wrap; align-content: start; gap: var(--space-sm); }
.checks > span { flex-basis: 100%; }
.checks label, .check { display: inline-flex; align-items: center; gap: 6px; }
fieldset { margin: var(--space-lg) 0 0; padding: var(--space-md); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); }
legend { padding: 0 var(--space-xs); color: var(--color-text-secondary); }
.check { margin: 6px var(--space-md) 6px 0; }
.inline-actions { margin-top: var(--space-lg); }
@media (max-width: 760px) { .section-head { display: grid; } .form-grid { grid-template-columns: 1fr; } .wide { grid-column: auto; } }
</style>
